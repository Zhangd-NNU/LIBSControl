import unittest

from libs_laser.protocol import (
    Command,
    Frame,
    FrameStreamParser,
    ProtocolError,
    TRAILER,
    link_frame,
    voltage_frame,
)


class ProtocolTests(unittest.TestCase):
    def test_link_frame_matches_manual(self):
        self.assertEqual(link_frame(True).to_bytes(), bytes.fromhex("01 11 00 00 00 00 CC 33 C3 3C"))
        self.assertEqual(link_frame(False).to_bytes(), bytes.fromhex("01 11 00 11 00 00 CC 33 C3 3C"))

    def test_voltage_encoding_matches_680v_example(self):
        self.assertEqual(voltage_frame(680).to_bytes(), bytes.fromhex("01 22 02 A8 00 00 CC 33 C3 3C"))

    def test_voltage_range(self):
        with self.assertRaises(ProtocolError):
            voltage_frame(0)
        with self.assertRaises(ProtocolError):
            voltage_frame(1001)

    def test_frame_round_trip(self):
        frame = Frame(Command.FREQUENCY, 0, 10)
        self.assertEqual(Frame.from_bytes(frame.to_bytes()), frame)

    def test_parser_accepts_fragmented_and_stuck_frames(self):
        parser = FrameStreamParser()
        first = link_frame(True).to_bytes()
        second = voltage_frame(850).to_bytes()
        self.assertEqual(parser.feed(b"noise" + first[:4]), [])
        parsed = parser.feed(first[4:] + second)
        self.assertEqual(parsed, [link_frame(True), voltage_frame(850)])

    def test_parser_skips_bad_trailer(self):
        parser = FrameStreamParser()
        bad = bytes((1, 0x55, 0, 0x55, 0, 0)) + b"BAD!"
        good = Frame(Command.PREFIRE, 0, 0x55).to_bytes()
        self.assertEqual(parser.feed(bad + good), [Frame(Command.PREFIRE, 0, 0x55)])

    def test_manual_trailer_constant(self):
        self.assertEqual(TRAILER, bytes.fromhex("CC 33 C3 3C"))


if __name__ == "__main__":
    unittest.main()
