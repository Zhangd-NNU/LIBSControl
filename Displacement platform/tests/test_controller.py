import unittest

from libs_stage.controller import FeinixsController, StageError
from libs_stage.paths import Point


class ControllerTests(unittest.TestCase):
    def test_simulated_connection_discovers_three_axes(self):
        controller = FeinixsController("unused.dll", simulate=True)
        controller.connect("COM3", 19200)
        self.assertEqual(controller.available_axes, {"1", "2", "3"})
        self.assertTrue(all(info["model"] == "SIM-IMC" for info in controller.axis_info.values()))

    def test_connection_defines_current_position_as_zero(self):
        controller = FeinixsController("unused.dll", simulate=True)
        controller.positions.update({"1": 8.0, "2": -4.0, "3": 2.5})
        controller.connect("COM3", 115200)
        self.assertEqual(controller.positions, {"1": 0.0, "2": 0.0, "3": 0.0})

    def test_work_coordinate_is_relative_to_connection_origin(self):
        controller = FeinixsController("unused.dll", simulate=True)
        controller.positions["1"] = 6.524
        controller.connect("COM3", 115200)
        self.assertAlmostEqual(controller.origin_positions["1"], 6.524)
        self.assertEqual(controller.get_position("1"), 0.0)
        controller.move_axis_relative("1", 1.0, 5.0)
        self.assertAlmostEqual(controller.raw_positions["1"], 7.524)
        self.assertAlmostEqual(controller.positions["1"], 1.0)

    def test_unavailable_axis_is_rejected(self):
        controller = FeinixsController("unused.dll", simulate=True)
        controller.connect("COM3", 19200)
        controller.available_axes.remove("3")
        with self.assertRaises(StageError):
            controller.move_axis_relative("3", 1.0, 5.0)

    def test_zero_can_target_single_axis(self):
        controller = FeinixsController("unused.dll", simulate=True)
        controller.connect("COM3", 115200)
        controller.positions.update({"1": 1.0, "2": 2.0, "3": 3.0})
        controller.zero(("2",))
        self.assertEqual(controller.positions, {"1": 1.0, "2": 0.0, "3": 3.0})

    def test_home_can_target_single_axis(self):
        controller = FeinixsController("unused.dll", simulate=True)
        controller.connect("COM3", 115200)
        controller.positions.update({"1": 1.0, "2": 2.0, "3": 3.0})
        controller.home(("3",))
        self.assertEqual(controller.positions, {"1": 1.0, "2": 2.0, "3": 0.0})

    def test_home_moves_to_connection_origin_without_redefining_it(self):
        class MotionDll:
            position = 7.524
            targets = []

            @classmethod
            def fti_single_getpos(cls, handle, axis, value):
                value._obj.value = cls.position
                return 0

            @staticmethod
            def fti_single_setenabled(handle, axis, enabled): return 0

            @classmethod
            def fti_single_moveabs(cls, handle, axis, target):
                cls.targets.append(target.value)
                cls.position = target.value
                return 0

            @staticmethod
            def fti_single_getstatus(handle, axis, status):
                status._obj.value = 0
                return 0

            @staticmethod
            def fti_single_getlimits(status, limits):
                limits[0] = limits[1] = 0
                return 0

            @staticmethod
            def fti_single_isrunning(status, running):
                running._obj.value = 0
                return 0

        MotionDll.targets = []
        MotionDll.position = 7.524
        controller = FeinixsController("unused.dll")
        controller.dll = MotionDll()
        controller.connected = True
        controller.available_axes = {"1"}
        controller.origin_positions["1"] = 6.524
        controller.raw_positions["1"] = 7.524
        controller.positions["1"] = 1.0
        controller.home(("1",))
        self.assertAlmostEqual(MotionDll.targets[0], 6.524, places=5)
        self.assertAlmostEqual(controller.origin_positions["1"], 6.524)
        self.assertAlmostEqual(controller.positions["1"], 0.0, places=5)

    def test_manual_move_reports_positive_limit(self):
        controller = FeinixsController("unused.dll", simulate=True)
        controller.connect("COM3", 115200)
        with self.assertRaisesRegex(StageError, "达到正向极限"):
            controller.move_axis_relative("1", 50.0, 5.0)

    def test_manual_move_reports_negative_limit(self):
        controller = FeinixsController("unused.dll", simulate=True)
        controller.connect("COM3", 115200)
        with self.assertRaisesRegex(StageError, "达到负向极限"):
            controller.move_axis_relative("3", -50.0, 5.0)

    def test_manual_speed_above_safe_limit_is_rejected(self):
        controller = FeinixsController("unused.dll", simulate=True)
        controller.connect("COM3", 115200)
        with self.assertRaisesRegex(StageError, "超过安全上限"):
            controller.move_axis_relative("1", 1.0, 10.1)

    def test_scan_speed_above_safe_limit_is_rejected(self):
        controller = FeinixsController("unused.dll", simulate=True)
        controller.connect("COM3", 115200)
        with self.assertRaisesRegex(StageError, "超过安全上限"):
            controller.move_xy(Point(1.0, 1.0), 10.1)

    def test_discovery_accepts_only_one_axis(self):
        class OneAxisDll:
            @staticmethod
            def fti_single_getpos(handle, axis, value):
                if axis != b"2":
                    return 1
                value._obj.value = 12.5
                return 0

            @staticmethod
            def fti_get_motor_model(handle, axis, buffer): return 1
            @staticmethod
            def fti_get_pitch(handle, axis, value): return 1
            @staticmethod
            def fti_get_div(handle, axis, value): return 1
            @staticmethod
            def fti_get_vel(handle, axis, value): return 1
            @staticmethod
            def fti_get_sw_p1(handle, axis, value): return 1
            @staticmethod
            def fti_get_sw_p2(handle, axis, value): return 1

        controller = FeinixsController("unused.dll")
        controller.dll = OneAxisDll()
        info = controller.discover_axes()
        self.assertEqual(controller.available_axes, {"2"})
        self.assertEqual(info["2"]["position_mm"], 12.5)

    def test_discovery_rejects_no_axes(self):
        class NoAxisDll:
            @staticmethod
            def fti_single_getpos(handle, axis, value): return 1

        controller = FeinixsController("unused.dll")
        controller.dll = NoAxisDll()
        with self.assertRaises(StageError):
            controller.discover_axes()


if __name__ == "__main__":
    unittest.main()
