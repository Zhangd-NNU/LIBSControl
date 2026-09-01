from __future__ import annotations

import csv
import queue
import tempfile
import time
import unittest
from pathlib import Path

from PIL import Image

from libs_camera.controller import CameraError, CameraSystem
from libs_camera.models import MONITOR, ROLES, TRIGGER_A, TRIGGER_B, CameraSettings, SaveSettings


class CameraSystemSimulationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.events: queue.Queue[dict] = queue.Queue()
        self.system = CameraSystem(self.events.put)
        self.devices = self.system.enumerate_devices(simulation=True)
        self.assignments = {role: self.devices[index].uid for index, role in enumerate(ROLES)}
        self.settings = {
            TRIGGER_A: CameraSettings(exposure_ms=5.0),
            TRIGGER_B: CameraSettings(exposure_ms=7.0, trigger_edge=1),
            MONITOR: CameraSettings(auto_exposure=True),
        }

    def tearDown(self) -> None:
        self.system.close()

    def test_enumerates_three_stable_virtual_devices(self) -> None:
        self.assertEqual(3, len(self.devices))
        self.assertEqual(3, len({device.serial for device in self.devices}))
        self.assertTrue(all(device.simulated for device in self.devices))

    def test_rejects_duplicate_role_assignment(self) -> None:
        duplicate = dict(self.assignments)
        duplicate[TRIGGER_B] = duplicate[TRIGGER_A]
        with self.assertRaises(CameraError):
            self.system.connect(duplicate, self.settings)

    def test_rejects_zero_camera_connection(self) -> None:
        with self.assertRaises(CameraError):
            self.system.connect({}, {})

    def test_monitor_produces_preview(self) -> None:
        self.system.connect(self.assignments, self.settings)
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            try:
                event = self.events.get(timeout=0.2)
            except queue.Empty:
                continue
            if event.get("type") == "preview" and event.get("role") == MONITOR:
                self.assertIsInstance(event["image"], Image.Image)
                return
        self.fail("monitor preview event was not emitted")

    def test_single_monitor_camera_connects_and_previews(self) -> None:
        self.system.connect(
            {MONITOR: self.devices[0].uid},
            {MONITOR: CameraSettings(auto_exposure=True)},
        )
        self.assertEqual({MONITOR}, set(self.system.endpoints))
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            try:
                event = self.events.get(timeout=0.2)
            except queue.Empty:
                continue
            if event.get("type") == "preview" and event.get("role") == MONITOR:
                return
        self.fail("single monitor camera did not produce preview")

    def test_two_selected_roles_connect_without_third_camera(self) -> None:
        assignments = {
            TRIGGER_A: self.devices[0].uid,
            MONITOR: self.devices[1].uid,
        }
        self.system.connect(assignments, self.settings)
        self.assertEqual({TRIGGER_A, MONITOR}, set(self.system.endpoints))

    def test_single_trigger_camera_records(self) -> None:
        self.system.connect({TRIGGER_B: self.devices[0].uid}, self.settings)
        with tempfile.TemporaryDirectory() as temporary:
            session = self.system.start_recording(
                SaveSettings(Path(temporary), folder_name="single_camera", file_name="sample", image_format="PNG")
            )
            self.system.software_trigger()
            deadline = time.monotonic() + 3.0
            while time.monotonic() < deadline:
                if self.system.stats_snapshot()[TRIGGER_B]["saved"] >= 1:
                    break
                time.sleep(0.05)
            self.system.stop_recording()
            self.assertFalse((session / "Camera A").exists())
            self.assertEqual(["sample-1.png"], [path.name for path in (session / "Camera B").glob("*.png")])

    def test_trigger_preview_only_does_not_write_files(self) -> None:
        self.system.connect({TRIGGER_A: self.devices[0].uid}, self.settings)
        with tempfile.TemporaryDirectory() as temporary:
            output_root = Path(temporary)
            session = self.system.start_recording(
                SaveSettings(
                    output_root,
                    folder_name="must_not_exist",
                    file_name="must_not_exist",
                    image_format="PNG",
                    save_enabled=False,
                )
            )
            self.assertIsNone(session)
            for _ in range(5):
                self.system.software_trigger(TRIGGER_A)
            deadline = time.monotonic() + 3.0
            while time.monotonic() < deadline:
                if self.system.stats_snapshot()[TRIGGER_A]["received"] >= 5:
                    break
                time.sleep(0.03)
            self.system.stop_recording()
            stats = self.system.stats_snapshot()[TRIGGER_A]
            self.assertEqual(5, stats["received"])
            self.assertEqual(0, stats["saved"])
            self.assertEqual([], list(output_root.iterdir()))

    def test_high_rate_trigger_saves_all_but_throttles_preview_events(self) -> None:
        self.system.connect({TRIGGER_A: self.devices[0].uid}, self.settings)
        expected_frames = 30
        with tempfile.TemporaryDirectory() as temporary:
            self.system.start_recording(
                SaveSettings(Path(temporary), folder_name="high_rate", file_name="frame", image_format="BMP")
            )
            for _ in range(expected_frames):
                self.system.software_trigger(TRIGGER_A)
            deadline = time.monotonic() + 6.0
            while time.monotonic() < deadline:
                if self.system.stats_snapshot()[TRIGGER_A]["saved"] >= expected_frames:
                    break
                time.sleep(0.03)
            self.system.stop_recording()
        self.assertEqual(expected_frames, self.system.stats_snapshot()[TRIGGER_A]["saved"])
        preview_events = 0
        while not self.events.empty():
            event = self.events.get_nowait()
            if event.get("type") == "preview" and event.get("role") == TRIGGER_A:
                preview_events += 1
        self.assertLess(preview_events, expected_frames)

    def test_two_trigger_cameras_save_images_and_manifests(self) -> None:
        self.system.connect(self.assignments, self.settings)
        with tempfile.TemporaryDirectory() as temporary:
            session = self.system.start_recording(
                SaveSettings(Path(temporary), folder_name="unit_test", file_name="spectrum", image_format="PNG")
            )
            expected_frames = 8
            for _ in range(expected_frames):
                self.system.software_trigger()
            deadline = time.monotonic() + 8.0
            while time.monotonic() < deadline:
                stats = self.system.stats_snapshot()
                if (stats[TRIGGER_A]["saved"] >= expected_frames
                        and stats[TRIGGER_B]["saved"] >= expected_frames):
                    break
                time.sleep(0.05)
            self.system.stop_recording()
            self.assertEqual("unit_test", session.name)
            for folder in ("Camera A", "Camera B"):
                images = list((session / folder).glob("*.png"))
                self.assertEqual(expected_frames, len(images))
                self.assertEqual(
                    {f"spectrum-{index}.png" for index in range(1, expected_frames + 1)},
                    {image.name for image in images},
                )
                with Image.open(images[0]) as image:
                    self.assertGreater(image.width, 0)
                    self.assertGreater(image.height, 0)
                with (session / folder / "manifest.csv").open(encoding="utf-8-sig", newline="") as stream:
                    rows = list(csv.DictReader(stream))
                self.assertEqual(expected_frames, len(rows))
                self.assertEqual({image.name for image in images}, {row["filename"] for row in rows})


if __name__ == "__main__":
    unittest.main()
