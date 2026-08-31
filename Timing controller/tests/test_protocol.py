import unittest

from libs_timing.protocol import Mode, STOP_FRAME, TimingParameters, build_parameter_frame


class ProtocolTests(unittest.TestCase):
    def test_default_frame_layout(self):
        frame = build_parameter_frame(TimingParameters())
        self.assertEqual(len(frame), 27)
        self.assertEqual(frame[:2], b"\xAA\xAA")
        self.assertEqual(frame[-2:], b"\xBB\xBB")
        self.assertEqual(frame[2], Mode.CONTINUOUS)
        self.assertEqual(frame[3:5], b"\x00\x00")
        self.assertEqual(frame[5:7], b"\x0A\x00")
        self.assertEqual(frame[7:9], b"\x0A\x00")
        self.assertEqual(frame[9:13], b"\x0A\x00\x00\x00")
        self.assertEqual(frame[21:25], b"\x01\x01\x01\x01")

    def test_fractional_frequency_and_little_endian(self):
        p = TimingParameters(mode=Mode.FINITE, pulse_count=513, frequency_hz=10.9,
                             pulse_width_us=1000, delays_us=(1, 2, 3),
                             delays_ns=(5, 10, 995))
        frame = build_parameter_frame(p)
        self.assertEqual(frame[3:5], b"\x01\x02")
        self.assertEqual(frame[5:7], b"\x0A\x09")
        self.assertEqual(frame[7:9], b"\xE8\x03")
        self.assertEqual(frame[9:13], b"\x01\x00\x05\x00")

    def test_invalid_nanosecond_step(self):
        with self.assertRaisesRegex(ValueError, "5 ns"):
            build_parameter_frame(TimingParameters(delays_ns=(1, 0, 0)))

    def test_finite_requires_count(self):
        with self.assertRaisesRegex(ValueError, "脉冲数"):
            build_parameter_frame(TimingParameters(mode=Mode.FINITE, pulse_count=0))

    def test_stop_frame(self):
        self.assertEqual(STOP_FRAME, b"\xDD\xDD")


if __name__ == "__main__":
    unittest.main()
