from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


TRIGGER_A = "trigger_a"
TRIGGER_B = "trigger_b"
MONITOR = "monitor"
ROLES = (TRIGGER_A, TRIGGER_B, MONITOR)
ROLE_NAMES = {
    TRIGGER_A: "触发相机 A",
    TRIGGER_B: "触发相机 B",
    MONITOR: "实时监控相机",
}


@dataclass(frozen=True)
class CameraDescriptor:
    uid: str
    serial: str
    friendly_name: str
    product_name: str
    port_type: str
    simulated: bool = False

    @property
    def display_name(self) -> str:
        return f"{self.friendly_name} · SN {self.serial} · {self.port_type}"


@dataclass
class CameraSettings:
    auto_exposure: bool = False
    exposure_ms: float = 10.0
    analog_gain: int = 1
    trigger_edge: int = 0
    trigger_delay_us: int = 0
    trigger_jitter_us: int = 0

    def validate(self, external_trigger: bool) -> None:
        if not self.auto_exposure:
            if not 0.001 <= self.exposure_ms <= 1_000_000:
                raise ValueError("曝光时间必须在 0.001 ms 到 1,000,000 ms 之间")
            if not 1 <= self.analog_gain <= 256:
                raise ValueError("模拟增益必须在 1 到 256 之间")
        if external_trigger and self.trigger_edge not in (0, 1, 2, 3):
            raise ValueError("外触发信号类型无效")
        if not 0 <= self.trigger_delay_us <= 2**32 - 1:
            raise ValueError("触发延时必须是非负整数（微秒）")
        if not 0 <= self.trigger_jitter_us <= 150_000:
            raise ValueError("触发去抖必须在 0 到 150000 微秒之间")


@dataclass
class SaveSettings:
    output_root: Path
    folder_name: str = ""
    file_name: str = ""
    image_format: str = "PNG"
    jpeg_quality: int = 92
    save_enabled: bool = True

    def validate(self) -> None:
        if not self.save_enabled:
            return
        self.image_format = self.image_format.upper()
        if self.image_format not in {"PNG", "JPG", "BMP"}:
            raise ValueError("图片格式必须是 PNG、JPG 或 BMP")
        if not 1 <= self.jpeg_quality <= 100:
            raise ValueError("JPG 质量必须在 1 到 100 之间")


@dataclass
class FrameMetadata:
    role: str
    serial: str
    sequence: int
    local_time: str
    sdk_timestamp_100us: int
    width: int
    height: int
    exposure_us: int
    analog_gain: float
    is_trigger: int


@dataclass
class CameraStats:
    received: int = 0
    saved: int = 0
    dropped: int = 0
    errors: int = 0
    last_frame_time: str = "--"
    last_filename: str = "--"

    def snapshot(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class AppConfig:
    simulation: bool = True
    output_root: str = "captures"
    image_format: str = "PNG"
    jpeg_quality: int = 92
    save_enabled: bool = True
    role_serials: dict[str, str] = field(default_factory=dict)
    camera_settings: dict[str, dict[str, Any]] = field(default_factory=lambda: {
        TRIGGER_A: asdict(CameraSettings(exposure_ms=10.0)),
        TRIGGER_B: asdict(CameraSettings(exposure_ms=10.0)),
        MONITOR: asdict(CameraSettings(auto_exposure=True, exposure_ms=10.0)),
    })

    def settings_for(self, role: str) -> CameraSettings:
        defaults = asdict(CameraSettings(auto_exposure=(role == MONITOR)))
        defaults.update(self.camera_settings.get(role, {}))
        return CameraSettings(**{key: defaults[key] for key in CameraSettings.__dataclass_fields__})
