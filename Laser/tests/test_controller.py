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
        self.controller.apply_parameters(850, 20, 4)
        self.assertEqual(self.controller.state.voltage_v, 850)
        self.assertEqual(self.controller.state.frequency_hz, 20)
        self.assertEqual(self.controller.state.divider, 4)
        self.assertEqual(self.controller.state.output_frequency_hz, 5)

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

    def test_single_trigger_flow(self):
        self.controller.set_single_mode(True)
        self.assertEqual(self.controller.state.divider, 0)
        self.controller.set_prefire(True)
        self.controller.set_work(True)
        self.controller.single_trigger()

    def test_single_mode_requires_internal_clock(self):
        self.controller.set_trigger_mode("external_no_q")
        with self.assertRaisesRegex(LaserError, "内时序"):
            self.controller.set_single_mode(True)

    def test_single_mode_rejects_continuous_q(self):
        self.controller.set_single_mode(True)
        self.controller.set_prefire(True)
        self.controller.set_work(True)
        with self.assertRaisesRegex(LaserError, "单次模式"):
            self.controller.set_q(True)

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


if __name__ == "__main__":
    unittest.main()
