import unittest

from libs_spectrometer.controller import (
    SimulationController,
    parse_oceanhood_announcement,
)


class ControllerTests(unittest.TestCase):
    def test_vendor_broadcast_parser(self):
        result = parse_oceanhood_announcement(b"oceanmodule:OH7020,SN:M240001\x00", "24.5.10.30")
        self.assertIsNotNone(result)
        self.assertEqual(result.model, "OH7020")
        self.assertEqual(result.serial, "M240001")
        self.assertEqual(result.port, 8888)

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


if __name__ == "__main__":
    unittest.main()
