from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from libs_camera.config import load_config, save_config
from libs_camera.models import AppConfig, TRIGGER_A


class ConfigTests(unittest.TestCase):
    def test_round_trip_unicode_config(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "config.json"
            original = AppConfig(
                output_root="实验图像",
                save_enabled=False,
                role_serials={TRIGGER_A: "SN-001"},
            )
            save_config(path, original)
            loaded = load_config(path)
            self.assertEqual("实验图像", loaded.output_root)
            self.assertEqual("SN-001", loaded.role_serials[TRIGGER_A])
            self.assertFalse(loaded.save_enabled)

    def test_invalid_top_level_shape_falls_back_to_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "config.json"
            path.write_text("[]", encoding="utf-8")
            loaded = load_config(path)
            self.assertTrue(loaded.simulation)


if __name__ == "__main__":
    unittest.main()
