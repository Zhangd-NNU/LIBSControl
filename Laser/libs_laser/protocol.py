from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum


ADDRESS = 0x01
FRAME_LENGTH = 10
TRAILER = bytes((0xCC, 0x33, 0xC3, 0x3C))


class ProtocolError(ValueError):
    pass


class Command(IntEnum):
    LINK = 0x11
    VOLTAGE = 0x22
    DIVIDER = 0x33
    FREQUENCY = 0x44
    PREFIRE = 0x55
    WORK = 0x66
    CLOCK = 0x77
    FAULT = 0x88
    SINGLE_MODE = 0xAA
    Q_SWITCH = 0xBB


@dataclass(frozen=True, slots=True)
class Frame:
    command: int
    data0: int = 0
    data1: int = 0
    data2: int = 0
    data3: int = 0
    address: int = ADDRESS

    def __post_init__(self) -> None:
        values = (self.address, self.command, self.data0, self.data1, self.data2, self.data3)
        if any(not isinstance(value, int) or not 0 <= value <= 0xFF for value in values):
            raise ProtocolError("帧字段必须是 0-255 的整数")
        if self.address != ADDRESS:
            raise ProtocolError(f"不支持的设备地址 0x{self.address:02X}")

    @property
    def data(self) -> tuple[int, int, int, int]:
        return self.data0, self.data1, self.data2, self.data3

    def to_bytes(self) -> bytes:
        return bytes((self.address, self.command, *self.data)) + TRAILER

    def hex_text(self) -> str:
        return self.to_bytes().hex(" ").upper()

    @classmethod
    def from_bytes(cls, raw: bytes | bytearray | memoryview) -> "Frame":
        packet = bytes(raw)
        if len(packet) != FRAME_LENGTH:
            raise ProtocolError(f"帧长度应为 {FRAME_LENGTH} 字节，实际为 {len(packet)}")
        if packet[0] != ADDRESS:
            raise ProtocolError(f"设备地址应为 0x{ADDRESS:02X}")
        if packet[6:] != TRAILER:
            raise ProtocolError("帧尾不正确")
        return cls(packet[1], packet[2], packet[3], packet[4], packet[5], packet[0])


class FrameStreamParser:
    """从可能分片、粘包或带噪声的串口字节流中提取固定十字节帧。"""

    def __init__(self) -> None:
        self._buffer = bytearray()

    def reset(self) -> None:
        self._buffer.clear()

    def feed(self, chunk: bytes | bytearray | memoryview) -> list[Frame]:
        if chunk:
            self._buffer.extend(chunk)
        frames: list[Frame] = []
        while len(self._buffer) >= FRAME_LENGTH:
            try:
                start = self._buffer.index(ADDRESS)
            except ValueError:
                self._buffer.clear()
                break
            if start:
                del self._buffer[:start]
            if len(self._buffer) < FRAME_LENGTH:
                break
            candidate = bytes(self._buffer[:FRAME_LENGTH])
            if candidate[6:] == TRAILER:
                frames.append(Frame.from_bytes(candidate))
                del self._buffer[:FRAME_LENGTH]
            else:
                del self._buffer[0]
        return frames


def link_frame(online: bool) -> Frame:
    return Frame(Command.LINK, 0, 0 if online else 0x11)


def voltage_frame(voltage: int) -> Frame:
    if not 1 <= voltage <= 1000:
        raise ProtocolError("本振电压必须在 1-1000 V 范围内")
    return Frame(Command.VOLTAGE, (voltage >> 8) & 0xFF, voltage & 0xFF)


def byte_value_frame(command: Command, value: int) -> Frame:
    if not 0 <= value <= 0xFF:
        raise ProtocolError("参数必须在 0-255 范围内")
    return Frame(command, 0, value)


def switch_frame(command: Command, enabled: bool) -> Frame:
    on_values = {
        Command.PREFIRE: 0x55,
        Command.WORK: 0x66,
        Command.SINGLE_MODE: 0xAA,
        Command.Q_SWITCH: 0xBB,
    }
    try:
        value = on_values[command] if enabled else 0
    except KeyError as exc:
        raise ProtocolError(f"命令 0x{int(command):02X} 不是开关命令") from exc
    return byte_value_frame(command, value)


def single_trigger_frame() -> Frame:
    return byte_value_frame(Command.WORK, 0x11)


def describe_frame(frame: Frame) -> str:
    command = frame.command
    value = frame.data1
    if command == Command.LINK:
        if frame.data0 == 0 and value == 0x11:
            return "联机断开"
        voltage = (frame.data0 << 8) | frame.data1
        return f"联机成功（电压 {voltage} V，频率 {frame.data3} Hz）"
    if command == Command.VOLTAGE:
        return f"本振电压 {(frame.data0 << 8) | frame.data1} V"
    if command == Command.DIVIDER:
        return f"分频系数 {value}"
    if command == Command.FREQUENCY:
        return f"灯频率 {value} Hz"
    if command == Command.PREFIRE:
        return "预燃开启" if value == 0x55 else "预燃关闭"
    if command == Command.WORK:
        if value == 0x11:
            return "单次触发"
        return "工作开启" if value == 0x66 else "工作停止"
    if command == Command.CLOCK:
        return {
            0x00: "内时序",
            0x77: "外时序开启",
            0x01: "外时序（外部 Q）",
            0x02: "外时序（内部 Q）",
        }.get(value, f"时序模式 0x{value:02X}")
    if command == Command.SINGLE_MODE:
        return "单次模式开启" if value == 0xAA else "单次模式关闭"
    if command == Command.Q_SWITCH:
        return "Q 开关开启（激光输出）" if value == 0xBB else "Q 开关关闭"
    if command == Command.FAULT:
        return {0x01: "水流故障", 0x02: "门开关联锁故障"}.get(value, f"未知故障 0x{value:02X}")
    return f"未知命令 0x{command:02X}"
