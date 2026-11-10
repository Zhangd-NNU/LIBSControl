import threading
import unittest
from unittest.mock import patch

from libs_spectrometer.controller import (
    SeaSDKController,
    SimulationController,
    NetworkDevice,
    parse_oceanhood_announcement,
    probe_network_devices,
)


class ControllerTests(unittest.TestCase):
    def test_vendor_broadcast_parser(self):
        result = parse_oceanhood_announcement(b"oceanmodule:OH7020,SN:M240001\x00", "24.5.10.30")
        self.assertIsNotNone(result)
        self.assertEqual(result.model, "OH7020")
        self.assertEqual(result.serial, "M240001")
        self.assertEqual(result.port, 8888)

    def test_active_probe_keeps_only_reachable_devices(self):
        candidates = [
            NetworkDevice("OH7020", "24.5.10.30"),
            NetworkDevice("OH7020", "24.5.10.31"),
        ]

        def ping(command, **kwargs):
            return_code = 1 if command[-1].endswith(".30") else 0
            return type("Result", (), {"returncode": return_code})()

        with patch("libs_spectrometer.controller.subprocess.run", side_effect=ping):
            found = probe_network_devices(candidates)

        self.assertEqual([device.ip for device in found], ["24.5.10.31"])

    def test_simulator_provides_four_channels(self):
        controller = SimulationController(seed=1)
        devices = controller.connect_usb()
        controller.configure(2000, 2, 0, 0, 2.0)
        spectra = controller.acquire()
        self.assertEqual(len(devices), 4)
        self.assertEqual(len(spectra), 4)
        self.assertTrue(all(len(item.wavelengths) == 1024 for item in spectra))
        controller.disconnect()

    def test_simulator_external_trigger_can_be_cancelled(self):
        controller = SimulationController(seed=1)
        controller.connect_usb()
        controller.configure(2000, 1, 0, 2, 0.0)
        controller.cancel_external_trigger()
        with self.assertRaisesRegex(Exception, "取消"):
            controller.acquire()

    def test_disconnect_waits_for_active_sdk_call(self):
        close_called = threading.Event()
        release_sdk_call = threading.Event()
        sdk_call_started = threading.Event()

        class FakeLibrary:
            @staticmethod
            def seasdk_close_all_spectrometers(error):
                close_called.set()

        controller = SeaSDKController.__new__(SeaSDKController)
        controller.lib = FakeLibrary()
        controller._lock = threading.RLock()
        controller.devices = [object()]
        controller._wavelengths = {0: (1.0,)}
        controller.connected = True

        def active_sdk_call():
            with controller._lock:
                sdk_call_started.set()
                release_sdk_call.wait(2.0)

        active = threading.Thread(target=active_sdk_call)
        active.start()
        self.assertTrue(sdk_call_started.wait(1.0))

        closing = threading.Thread(target=controller.disconnect)
        closing.start()
        self.assertFalse(close_called.wait(0.1))
        release_sdk_call.set()
        closing.join(1.0)
        active.join(1.0)

        self.assertTrue(close_called.is_set())
        self.assertFalse(controller.connected)
        self.assertEqual(controller.devices, [])

    def test_disconnect_before_first_connection_ignores_device_not_found(self):
        class EmptyLibrary:
            @staticmethod
            def seasdk_close_all_spectrometers(error):
                error._obj.value = 2

        controller = SeaSDKController.__new__(SeaSDKController)
        controller.lib = EmptyLibrary()
        controller._lock = threading.RLock()
        controller.devices = []
        controller._wavelengths = {}
        controller.connected = False

        controller.disconnect()

        self.assertFalse(controller.connected)
        self.assertEqual(controller.devices, [])

    def test_disconnect_after_connection_ignores_already_closed_result(self):
        class ClosedLibrary:
            @staticmethod
            def seasdk_close_all_spectrometers(error):
                error._obj.value = 2

        controller = SeaSDKController.__new__(SeaSDKController)
        controller.lib = ClosedLibrary()
        controller._lock = threading.RLock()
        controller.devices = [object()]
        controller._wavelengths = {32: (1.0,)}
        controller.connected = True

        controller.disconnect()

        self.assertFalse(controller.connected)
        self.assertEqual(controller.devices, [])


if __name__ == "__main__":
    unittest.main()
