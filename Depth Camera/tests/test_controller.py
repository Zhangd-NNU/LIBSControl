import tempfile
import threading
import unittest
from pathlib import Path

from libs_camera.controller import (
    CameraError,
    CameraSettings,
    DepthCameraController,
    FramePacket,
    depth_to_rgb,
    np,
)


class CameraSettingsTests(unittest.TestCase):
    def test_default_configuration_is_valid(self):
        CameraSettings().validate()

    def test_alignment_requires_color_and_depth(self):
        with self.assertRaisesRegex(CameraError, "深度对齐"):
            CameraSettings(enable_color=False, enable_depth=True, align_depth=True).validate()

    def test_at_least_one_stream_is_required(self):
        with self.assertRaisesRegex(CameraError, "至少启用"):
            CameraSettings(enable_color=False, enable_depth=False, enable_infrared=False, align_depth=False).validate()

    def test_invalid_depth_range_is_rejected(self):
        with self.assertRaisesRegex(CameraError, "0.1–20"):
            CameraSettings(max_distance_m=30).validate()


@unittest.skipIf(np is None, "NumPy 未安装")
class SimulationTests(unittest.TestCase):
    def setUp(self):
        self.controller = DepthCameraController(simulate=True)

    def tearDown(self):
        self.controller.stop()

    def test_simulated_device_is_discoverable(self):
        devices = DepthCameraController.list_devices(include_simulated=True)
        self.assertTrue(any(device.simulated for device in devices))

    def test_depth_colormap_keeps_invalid_pixels_black(self):
        raw = np.array([[0, 500], [1000, 2000]], dtype=np.uint16)
        rgb = depth_to_rgb(raw, 0.001, 2.0)
        self.assertEqual(rgb.shape, (2, 2, 3))
        self.assertEqual(rgb[0, 0].tolist(), [0, 0, 0])

    def test_simulation_stream_and_measurement(self):
        ready = threading.Event()
        self.controller.start(CameraSettings(width=160, height=120, fps=15), on_frame=lambda _: ready.set())
        self.assertTrue(ready.wait(2.0))
        measurement = self.controller.measure(80, 60)
        self.assertGreater(measurement.distance_m, 0)
        self.assertAlmostEqual(measurement.point_z_m, measurement.distance_m)

    def test_measurement_uses_displayed_packet_and_nearest_valid_depth(self):
        raw = np.zeros((3, 3), dtype=np.uint16)
        raw[1, 2] = 1250
        packet = FramePacket(None, None, None, raw, 0.001, 7, 0.0, 0.0, 0.0)
        measurement = self.controller.measure(1, 1, packet=packet, search_radius=1)
        self.assertEqual((measurement.pixel_x, measurement.pixel_y), (2, 1))
        self.assertAlmostEqual(measurement.distance_m, 1.25)

    def test_simulation_exports_ply(self):
        ready = threading.Event()
        self.controller.start(CameraSettings(width=80, height=60, fps=10), on_frame=lambda _: ready.set())
        self.assertTrue(ready.wait(2.0))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cloud.ply"
            self.controller.export_ply(str(path))
            self.assertTrue(path.is_file())
            self.assertIn("element vertex", path.read_text(encoding="ascii")[:200])


if __name__ == "__main__":
    unittest.main()
