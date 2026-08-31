from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum


class Mode(IntEnum):
    FINITE = 0x00
    CONTINUOUS = 0x01
    EXTERNAL_TRIGGER = 0x02


HEADER = b"\xAA\xAA"
TAIL = b"\xBB\xBB"
STOP_FRAME = b"\xDD\xDD"


@dataclass(frozen=True)
class TimingParameters:
    mode: Mode = Mode.CONTINUOUS
    pulse_count: int = 0
    frequency_hz: float = 10.0
    pulse_width_us: int = 10
    delays_us: tuple[int, int, int] = (10, 0, 0)
    delays_ns: tuple[int, int, int] = (0, 0, 0)
    loads_50ohm: tuple[bool, bool, bool, bool] = (True, True, True, True)

    def validate(self) -> None:
        if self.mode not in Mode:
            raise ValueError("未知运行模式")
        if not 0 <= self.pulse_count <= 65535:
            raise ValueError("脉冲数必须在 0–65535 之间")
        if not 0.1 <= self.frequency_hz <= 20.0:
            raise ValueError("频率必须在 0.1–20.0 Hz 之间")
        if round(self.frequency_hz * 10) != self.frequency_hz * 10:
            raise ValueError("频率分辨率为 0.1 Hz")
        if not 2 <= self.pulse_width_us <= 65535:
            raise ValueError("脉宽必须在 2–65535 μs 之间")
        if len(self.delays_us) != 3 or len(self.delays_ns) != 3:
            raise ValueError("必须提供 T1、T2、T3 三路延时")
        for index, value in enumerate(self.delays_us, 1):
            if not 0 <= value <= 65535:
                raise ValueError(f"T{index} 微秒延时必须在 0–65535 之间")
        for index, value in enumerate(self.delays_ns, 1):
            if not 0 <= value <= 999 or value % 5:
                raise ValueError(f"T{index} 纳秒延时必须是 0–999 范围内的 5 ns 整数倍")
        if len(self.loads_50ohm) != 4:
            raise ValueError("必须提供四路阻抗设置")
        if self.mode == Mode.FINITE and self.pulse_count == 0:
            raise ValueError("脉冲模式下脉冲数必须大于 0")


def _u16(value: int) -> bytes:
    return value.to_bytes(2, "little", signed=False)


def build_parameter_frame(parameters: TimingParameters) -> bytes:
    """根据厂商 HCLDG_V3 文档生成参数/运行帧。

    文档称参数区为 22 字节，但逐项字段合计为 23 字节。本实现严格按
    文档列出的所有字段编码：1+2+2+2+3*4+4 = 23 字节。
    """
    parameters.validate()
    integer_hz = int(parameters.frequency_hz)
    decimal_tenths = int(round((parameters.frequency_hz - integer_hz) * 10))
    body = bytearray([int(parameters.mode)])
    body.extend(_u16(parameters.pulse_count))
    body.extend((integer_hz, decimal_tenths))
    body.extend(_u16(parameters.pulse_width_us))
    for delay_us, delay_ns in zip(parameters.delays_us, parameters.delays_ns):
        body.extend(_u16(delay_us))
        body.extend(_u16(delay_ns))
    body.extend(1 if selected else 0 for selected in parameters.loads_50ohm)
    return HEADER + bytes(body) + TAIL


def frame_hex(frame: bytes) -> str:
    return " ".join(f"{byte:02X}" for byte in frame)
