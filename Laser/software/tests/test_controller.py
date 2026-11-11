import time
import unittest

from libs_laser.controller import LaserController, LaserError
from libs_laser.serial_port import SimulatedTransport


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.transport = SimulatedTransport(response_delay=0.001)
        self.controller = LaserController(self.transport, timeout=0.2, retries=0)
        self.controller.connect()
        self.controller.set_online(True)

    def tearDown(self):
        self.controller.disconnect(safe=False)

    def test_online_response_uploads_voltage_and_frequency(self):
        self.assertTrue(self.controller.state.online)
        self.assertEqual(self.controller.state.voltage_v, 680)
        self.assertEqual(self.controller.state.frequency_hz, 20)

    def test_parameter_application(self):
        self.controller.apply_parameters(850, 4)
        self.assertEqual(self.controller.state.voltage_v, 850)
        self.assertEqual(self.controller.state.frequency_hz, 20)
        self.assertEqual(self.controller.state.divider, 4)
        self.assertEqual(self.controller.state.output_frequency_hz, 5)

    def test_parameter_application_leaves_divider_unchanged_by_default(self):
        self.controller.set_divider(4)
        self.controller.apply_parameters(850, 4)
        self.assertEqual(self.controller.state.divider, 4)

    def test_reapplying_current_values_does_not_wait_for_device_reply(self):
        self.controller.apply_parameters(680, 1, 20)
        self.assertEqual(self.controller.state.voltage_v, 680)
        self.assertEqual(self.controller.state.divider, 1)

    def test_frequency_is_only_changed_when_explicitly_supplied(self):
        self.controller.apply_parameters(850, 4)
        self.assertEqual(self.controller.state.frequency_hz, 20)
        self.controller.apply_parameters(850, 4, 25)
        self.assertEqual(self.controller.state.frequency_hz, 25)

    def test_normal_start_and_stop_sequence(self):
        self.controller.set_prefire(True)
        self.controller.set_work(True)
        self.controller.set_q(True)
        self.assertTrue(self.controller.state.output_active)
        self.controller.emergency_stop()
        self.assertFalse(self.controller.state.prefire)
        self.assertFalse(self.controller.state.working)
        self.assertFalse(self.controller.state.q_enabled)

    def test_work_requires_prefire(self):
        with self.assertRaisesRegex(LaserError, "先开启预燃"):
            self.controller.set_work(True)

    def test_q_requires_work(self):
        self.controller.set_prefire(True)
        with self.assertRaisesRegex(LaserError, "预燃和工作"):
            self.controller.set_q(True)

    def test_parameters_are_locked_during_work(self):
        self.controller.set_prefire(True)
        self.controller.set_work(True)
        with self.assertRaisesRegex(LaserError, "禁止修改参数"):
            self.controller.set_voltage(700)

    def test_external_q_mode_uses_two_step_protocol(self):
        self.controller.set_trigger_mode("external_q")
        self.assertEqual(self.controller.state.trigger_mode, "external_q")

    def test_external_q_mode_rejects_serial_q_enable(self):
        self.controller.set_trigger_mode("external_q")
        self.controller.set_prefire(True)
        self.controller.set_work(True)
        with self.assertRaisesRegex(LaserError, "Q IN"):
            self.controller.set_q(True)

    def test_divider_zero_selects_single_mode(self):
        self.controller.set_divider(0)
        self.assertEqual(self.controller.state.divider, 0)
        self.assertTrue(self.controller.state.single_mode)

    def test_nonzero_divider_exits_single_mode(self):
        self.controller.set_divider(0)
        self.controller.set_divider(4)
        self.assertFalse(self.controller.state.single_mode)

    def test_fault_locks_start(self):
        self.transport.inject_fault(1)
        self.assertTrue(self.controller.state.water_fault)
        with self.assertRaisesRegex(LaserError, "水流故障"):
            self.controller.set_prefire(True)

    def test_fault_triggers_automatic_safe_stop(self):
        self.controller.set_prefire(True)
        self.controller.set_work(True)
        self.controller.set_q(True)
        self.transport.inject_fault(2)
        deadline = time.monotonic() + 0.2
        while self.controller.state.output_active and time.monotonic() < deadline:
            time.sleep(0.005)
        self.assertTrue(self.controller.state.door_fault)
        self.assertFalse(self.controller.state.prefire)
        self.assertFalse(self.controller.state.working)
        self.assertFalse(self.controller.state.q_enabled)

    def test_disconnect_always_releases_serial_after_write_failure(self):
        def fail_write(_data):
            raise OSError("simulated write failure")

        self.transport.write = fail_write
        with self.assertRaises(LaserError):
            self.controller.disconnect(safe=True)
        self.assertFalse(self.transport.is_open)
        self.assertFalse(self.controller.state.serial_connected)


if __name__ == "__main__":
    unittest.main()
