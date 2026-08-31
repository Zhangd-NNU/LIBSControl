import tempfile
import unittest
from pathlib import Path

from libs_spectrometer.gui import save_frame_csv
from libs_spectrometer.processing import Spectrum, boxcar, build_processed_frame, stitch_spectra


class ProcessingTests(unittest.TestCase):
    def test_boxcar_keeps_length_and_smooths(self):
        result = boxcar((0.0, 0.0, 9.0, 0.0, 0.0), 3)
        self.assertEqual(len(result), 5)
        self.assertAlmostEqual(result[2], 3.0)

    def test_dark_is_subtracted_and_clamped(self):
        raw = Spectrum(1, (200.0, 201.0), (5.0, 15.0))
        dark = Spectrum(1, (200.0, 201.0), (8.0, 4.0))
        frame = build_processed_frame((raw,), {1: dark}, True)
        self.assertEqual(frame.displayed[0].intensities, (0.0, 11.0))

    def test_stitch_scales_overlapping_channel(self):
        left = Spectrum(1, (200.0, 210.0, 220.0, 230.0), (10.0, 20.0, 30.0, 40.0))
        right = Spectrum(2, (220.0, 230.0, 240.0, 250.0), (15.0, 20.0, 25.0, 30.0))
        stitched = stitch_spectra((right, left), "auto")
        self.assertIsNotNone(stitched)
        self.assertEqual(tuple(sorted(stitched.wavelengths)), stitched.wavelengths)
        self.assertEqual(stitched.wavelengths[0], 200.0)
        self.assertEqual(stitched.wavelengths[-1], 250.0)
        self.assertAlmostEqual(stitched.intensities[3], 40.0)

    def test_raw_stitch_preserves_selected_channel_intensities(self):
        left = Spectrum(1, (200.0, 210.0, 220.0, 230.0), (10.0, 20.0, 30.0, 40.0))
        right = Spectrum(2, (220.0, 230.0, 240.0, 250.0), (15.0, 20.0, 25.0, 30.0))
        stitched = stitch_spectra((left, right), "raw")
        self.assertEqual(stitched.wavelengths, (200.0, 210.0, 220.0, 230.0, 240.0, 250.0))
        self.assertEqual(stitched.intensities, (10.0, 20.0, 30.0, 20.0, 25.0, 30.0))

    def test_calibrated_stitch_applies_per_channel_factors(self):
        left = Spectrum(1, (200.0, 210.0, 220.0, 230.0), (10.0, 20.0, 30.0, 40.0))
        right = Spectrum(2, (220.0, 230.0, 240.0), (15.0, 20.0, 25.0))
        stitched = stitch_spectra((left, right), "calibrated", {1: 0.5, 2: 3.0})
        self.assertEqual(stitched.intensities, (5.0, 10.0, 15.0, 60.0, 75.0))

    def test_unknown_stitch_mode_is_rejected(self):
        spectrum = Spectrum(1, (200.0,), (10.0,))
        with self.assertRaisesRegex(ValueError, "未知拼接模式"):
            stitch_spectra((spectrum,), "invalid")

    def test_csv_contains_channel_and_stitched_columns(self):
        spectrum = Spectrum(1, (200.0, 201.0), (10.0, 20.0))
        frame = build_processed_frame((spectrum,), {}, False, sequence=3, metadata={"integration_us": 1000})
        with tempfile.TemporaryDirectory() as folder:
            target = save_frame_csv(frame, Path(folder) / "frame.csv")
            text = target.read_text(encoding="utf-8-sig")
        self.assertIn("ch1_wavelength_nm", text)
        self.assertIn("stitched_intensity_count", text)
        self.assertIn("# sequence=3", text)


if __name__ == "__main__":
    unittest.main()
