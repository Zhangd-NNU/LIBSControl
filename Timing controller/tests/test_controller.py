import unittest

from libs_timing.controller import TimingController
from libs_timing.protocol import STOP_FRAME, TimingParameters


class ControllerTests(unittest.TestCase):
    def test_simulation_full_workflow(self):
        entries = []
        controller = TimingController(entries.append)
        controller.open("COM_TEST", simulation=True)
        self.assertTrue(controller.connected)
        frame = controller.apply(TimingParameters())
        self.assertEqual(len(frame), 27)
        controller.run(TimingParameters())
        controller.stop()
        self.assertEqual(entries[-1].data, STOP_FRAME)
        self.assertIn("停止", entries[-1].note)
        controller.close()
        self.assertFalse(controller.connected)


if __name__ == "__main__":
    unittest.main()
