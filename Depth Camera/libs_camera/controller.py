from __future__ import annotations

import json
import math
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

try:
    import numpy as np
except ImportError:  # 启动界面时给出友好的依赖提示
    np = None  # type: ignore[assignment]

try:
    import pyrealsense2 as rs
except ImportError:
    rs = None  # type: ignore[assignment]


class CameraError(RuntimeError):
    """可直接展示给用户的相机错误。"""


@dataclass(frozen=True)
class DeviceInfo:
    name: str
    serial: str
    firmware: str = ""
    usb_type: str = ""
    product_line: str = ""
    product_id: str = ""
    simulated: bool = False

    @property
    def label(self) -> str:
        suffix = " · 仿真" if self.simulated else ""
        return f"{self.name}  [{self.serial}]{suffix}"


@dataclass(frozen=True)
class CameraSettings:
    width: int = 640
    height: int = 480
    fps: int = 30
    enable_color: bool = True
    enable_depth: bool = True
    enable_infrared: bool = False
    align_depth: bool = True
    spatial_filter: bool = True
    temporal_filter: bool = True
    hole_filling_filter: bool = False
    max_distance_m: float = 4.0

    def validate(self) -> None:
        if self.width <= 0 or self.height <= 0:
            raise CameraError("图像分辨率必须为正数")
        if self.fps <= 0 or self.fps > 120:
            raise CameraError("帧率必须在 1–120 FPS 之间")
        if not (self.enable_color or self.enable_depth or self.enable_infrared):
            raise CameraError("请至少启用一路图像流")
        if self.align_depth and not (self.enable_color and self.enable_depth):
            raise CameraError("深度对齐需要同时启用彩色流和深度流")
        if not 0.1 <= self.max_distance_m <= 20:
            raise CameraError("深度显示上限必须在 0.1–20 m 之间")


@dataclass
class FramePacket:
    color_rgb: Any | None
    depth_rgb: Any | None
    infrared_rgb: Any | None
    depth_raw: Any | None
    depth_scale: float
    frame_number: int
    timestamp_ms: float
    fps: float
    received_at: float
    intrinsics: Any | None = None
    device_serial: str = ""


@dataclass(frozen=True)
class Measurement:
    pixel_x: int
    pixel_y: int
    distance_m: float
    point_x_m: float
    point_y_m: float
    point_z_m: float

    @property
    def display(self) -> str:
        return (
            f"像素 ({self.pixel_x}, {self.pixel_y})  距离 {self.distance_m:.4f} m\n"
            f"三维坐标 X {self.point_x_m:.4f} m · Y {self.point_y_m:.4f} m · Z {self.point_z_m:.4f} m"
        )


def depth_to_rgb(depth_raw: Any, depth_scale: float, max_distance_m: float) -> Any:
    """把 Z16 深度转换为带黑色无效区的 Turbo 风格 RGB 图。"""
    if np is None:
        raise CameraError("缺少 NumPy，无法处理图像")
    meters = depth_raw.astype(np.float32) * float(depth_scale)
    value = np.clip(meters / max(max_distance_m, 0.001), 0.0, 1.0)
    # 轻量级连续伪彩映射，不依赖 OpenCV。
    r = np.clip(1.5 - np.abs(4.0 * value - 3.0), 0.0, 1.0)
    g = np.clip(1.5 - np.abs(4.0 * value - 2.0), 0.0, 1.0)
    b = np.clip(1.5 - np.abs(4.0 * value - 1.0), 0.0, 1.0)
    rgb = (np.dstack((r, g, b)) * 255.0).astype(np.uint8)
    rgb[depth_raw == 0] = 0
    return rgb


class DepthCameraController:
    """RealSense 数据流、参数控制、测距、录像与点云导出。"""

    SIM_SERIAL = "SIM-RS-001"

    def __init__(self, simulate: bool = False):
        self.simulate = simulate
        self.running = False
        self.settings = CameraSettings()
        self.device_info: DeviceInfo | None = None
        self.pipeline: Any | None = None
        self.profile: Any | None = None
        self.device: Any | None = None
        self._depth_sensor: Any | None = None
        self._color_sensor: Any | None = None
        self._align: Any | None = None
        self._spatial: Any | None = None
        self._temporal: Any | None = None
        self._hole: Any | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._frame_lock = threading.Lock()
        self._latest: FramePacket | None = None
        self._latest_frameset: Any | None = None
        self._on_frame: Callable[[FramePacket], None] | None = None
        self._on_error: Callable[[str], None] | None = None
        self.record_path: str | None = None

    @staticmethod
    def runtime_status() -> dict[str, bool]:
        return {"numpy": np is not None, "pyrealsense2": rs is not None}

    @classmethod
    def list_devices(cls, include_simulated: bool = False) -> list[DeviceInfo]:
        devices: list[DeviceInfo] = []
        if rs is not None:
            try:
                context = rs.context()
                for device in context.query_devices():
                    devices.append(cls._read_device_info(device))
            except Exception as exc:
                if not include_simulated:
                    raise CameraError(f"枚举 RealSense 设备失败：{exc}") from exc
        if include_simulated:
            devices.append(
                DeviceInfo(
                    name="RealSense D400 Simulation",
                    serial=cls.SIM_SERIAL,
                    firmware="2.58.1-sim",
                    usb_type="Virtual USB 3.2",
                    product_line="D400",
                    product_id="0B5C",
                    simulated=True,
                )
            )
        return devices

    @staticmethod
    def _read_device_info(device: Any) -> DeviceInfo:
        def read(key: Any) -> str:
            try:
                return device.get_info(key) if device.supports(key) else ""
            except Exception:
                return ""

        return DeviceInfo(
            name=read(rs.camera_info.name) or "RealSense 深度相机",
            serial=read(rs.camera_info.serial_number),
            firmware=read(rs.camera_info.firmware_version),
            usb_type=read(rs.camera_info.usb_type_descriptor),
            product_line=read(rs.camera_info.product_line),
            product_id=read(rs.camera_info.product_id),
        )

    def start(
        self,
        settings: CameraSettings,
        serial: str = "",
        source_file: str = "",
        record_path: str = "",
        on_frame: Callable[[FramePacket], None] | None = None,
        on_error: Callable[[str], None] | None = None,
    ) -> DeviceInfo:
        if self.running:
            raise CameraError("相机数据流已经启动")
        settings.validate()
        if np is None:
            raise CameraError("缺少 NumPy。请先运行 install.bat 安装运行依赖。")
        self.settings = settings
        self._on_frame = on_frame
        self._on_error = on_error
        self._stop.clear()
        self.record_path = record_path or None
        if self.simulate:
            self.device_info = next(d for d in self.list_devices(True) if d.simulated)
        else:
            self._start_hardware(serial, source_file, record_path)
        self.running = True
        target = self._simulation_loop if self.simulate else self._capture_loop
        self._thread = threading.Thread(target=target, name="RealSenseCapture", daemon=True)
        self._thread.start()
        return self.device_info  # type: ignore[return-value]

    def _start_hardware(self, serial: str, source_file: str, record_path: str) -> None:
        if rs is None:
            raise CameraError("缺少 pyrealsense2。请先运行 install.bat 安装相机 SDK 绑定。")
        config = rs.config()
        if source_file:
            source = Path(source_file)
            if not source.is_file():
                raise CameraError(f"回放文件不存在：{source}")
            config.enable_device_from_file(str(source), repeat_playback=False)
        else:
            if serial and serial != self.SIM_SERIAL:
                config.enable_device(serial)
            s = self.settings
            if s.enable_depth:
                config.enable_stream(rs.stream.depth, s.width, s.height, rs.format.z16, s.fps)
            if s.enable_color:
                config.enable_stream(rs.stream.color, s.width, s.height, rs.format.bgr8, s.fps)
            if s.enable_infrared:
                config.enable_stream(rs.stream.infrared, 1, s.width, s.height, rs.format.y8, s.fps)
        if record_path:
            destination = Path(record_path)
            destination.parent.mkdir(parents=True, exist_ok=True)
            config.enable_record_to_file(str(destination))
        pipeline = rs.pipeline()
        try:
            profile = pipeline.start(config)
        except Exception as exc:
            raise CameraError(
                "启动相机失败。请检查设备、USB 3.x 连接，以及所选分辨率/FPS 是否受支持。\n"
                f"SDK 信息：{exc}"
            ) from exc
        self.pipeline = pipeline
        self.profile = profile
        self.device = profile.get_device()
        self.device_info = self._read_device_info(self.device)
        try:
            self._depth_sensor = self.device.first_depth_sensor()
        except Exception:
            self._depth_sensor = None
        for sensor in self.device.query_sensors():
            try:
                name = sensor.get_info(rs.camera_info.name)
                if "RGB" in name or "Color" in name:
                    self._color_sensor = sensor
            except Exception:
                continue
        if self.settings.align_depth and self.settings.enable_color and self.settings.enable_depth:
            self._align = rs.align(rs.stream.color)
        self._spatial = rs.spatial_filter() if self.settings.spatial_filter else None
        self._temporal = rs.temporal_filter() if self.settings.temporal_filter else None
        self._hole = rs.hole_filling_filter() if self.settings.hole_filling_filter else None

    def _capture_loop(self) -> None:
        sample_start = time.monotonic()
        sample_count = 0
        measured_fps = 0.0
        try:
            while not self._stop.is_set():
                try:
                    frames = self.pipeline.wait_for_frames(1000)
                except Exception as exc:
                    if self._stop.is_set():
                        break
                    raise CameraError(f"等待相机帧失败：{exc}") from exc
                if self._align is not None:
                    frames = self._align.process(frames)
                depth_frame = frames.get_depth_frame() if self.settings.enable_depth else None
                color_frame = frames.get_color_frame() if self.settings.enable_color else None
                infrared_frame = frames.get_infrared_frame(1) if self.settings.enable_infrared else None
                if depth_frame:
                    for image_filter in (self._spatial, self._temporal, self._hole):
                        if image_filter is not None:
                            depth_frame = image_filter.process(depth_frame)
                sample_count += 1
                elapsed = time.monotonic() - sample_start
                if elapsed >= 1.0:
                    measured_fps = sample_count / elapsed
                    sample_count, sample_start = 0, time.monotonic()
                packet = self._make_packet(frames, depth_frame, color_frame, infrared_frame, measured_fps)
                with self._frame_lock:
                    self._latest = packet
                    self._latest_frameset = frames
                if self._on_frame:
                    self._on_frame(packet)
        except Exception as exc:
            self._report_error(str(exc))
        finally:
            # 数据流异常时也必须主动释放 USB 设备，避免下次启动显示“设备忙”。
            pipeline = self.pipeline
            if pipeline is not None and not self._stop.is_set():
                try:
                    pipeline.stop()
                except Exception:
                    pass
                self.pipeline = None
            self.running = False

    def _make_packet(
        self,
        frames: Any,
        depth_frame: Any,
        color_frame: Any,
        infrared_frame: Any,
        measured_fps: float,
    ) -> FramePacket:
        color_rgb = None
        depth_rgb = None
        infrared_rgb = None
        depth_raw = None
        depth_scale = 0.001
        intrinsics = None
        primary = depth_frame or color_frame or infrared_frame
        if self._depth_sensor is not None:
            try:
                depth_scale = float(self._depth_sensor.get_depth_scale())
            except Exception:
                pass
        if color_frame:
            color_bgr = np.asanyarray(color_frame.get_data())
            color_rgb = color_bgr[..., ::-1].copy()
        if depth_frame:
            depth_raw = np.asanyarray(depth_frame.get_data()).copy()
            depth_rgb = depth_to_rgb(depth_raw, depth_scale, self.settings.max_distance_m)
            try:
                intrinsics = depth_frame.profile.as_video_stream_profile().intrinsics
            except Exception:
                intrinsics = None
        if infrared_frame:
            infrared = np.asanyarray(infrared_frame.get_data())
            infrared_rgb = np.repeat(infrared[..., None], 3, axis=2)
        return FramePacket(
            color_rgb=color_rgb,
            depth_rgb=depth_rgb,
            infrared_rgb=infrared_rgb,
            depth_raw=depth_raw,
            depth_scale=depth_scale,
            frame_number=int(primary.get_frame_number()) if primary else 0,
            timestamp_ms=float(primary.get_timestamp()) if primary else 0.0,
            fps=measured_fps,
            received_at=time.time(),
            intrinsics=intrinsics,
            device_serial=self.device_info.serial if self.device_info else "",
        )

    def _simulation_loop(self) -> None:
        s = self.settings
        start = time.monotonic()
        frame_number = 0
        period = 1.0 / s.fps
        yy, xx = np.mgrid[0 : s.height, 0 : s.width]
        try:
            while not self._stop.is_set():
                tick = time.monotonic()
                frame_number += 1
                phase = tick - start
                cx = s.width * (0.5 + 0.22 * math.sin(phase * 0.8))
                cy = s.height * (0.5 + 0.16 * math.cos(phase * 0.65))
                radius = max(24.0, min(s.width, s.height) * 0.16)
                distance = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2)
                depth_m = 1.10 + 0.0012 * xx + 0.0007 * yy
                depth_m = np.where(distance < radius, 0.58 + distance * 0.0017, depth_m)
                holes = ((xx + yy + frame_number) % 191 == 0)
                depth_raw = np.where(holes, 0, depth_m * 1000).astype(np.uint16)
                color = np.empty((s.height, s.width, 3), dtype=np.uint8)
                color[..., 0] = np.clip(15 + xx * 85 / max(s.width, 1), 0, 255)
                color[..., 1] = np.clip(28 + yy * 95 / max(s.height, 1), 0, 255)
                color[..., 2] = 48
                grid = ((xx % 80 < 2) | (yy % 80 < 2))
                color[grid] = (31, 85, 101)
                color[distance < radius] = (25, 196, 230)
                infrared = np.clip(42 + yy * 150 / max(s.height, 1), 0, 255).astype(np.uint8)
                packet = FramePacket(
                    color_rgb=color if s.enable_color else None,
                    depth_rgb=depth_to_rgb(depth_raw, 0.001, s.max_distance_m) if s.enable_depth else None,
                    infrared_rgb=np.repeat(infrared[..., None], 3, axis=2) if s.enable_infrared else None,
                    depth_raw=depth_raw if s.enable_depth else None,
                    depth_scale=0.001,
                    frame_number=frame_number,
                    timestamp_ms=phase * 1000.0,
                    fps=float(s.fps),
                    received_at=time.time(),
                    intrinsics=None,
                    device_serial=self.SIM_SERIAL,
                )
                with self._frame_lock:
                    self._latest = packet
                if self._on_frame:
                    self._on_frame(packet)
                wait = period - (time.monotonic() - tick)
                if wait > 0:
                    self._stop.wait(wait)
        except Exception as exc:
            self._report_error(f"仿真数据流错误：{exc}")
        finally:
            self.running = False

    def _report_error(self, message: str) -> None:
        if self._on_error and not self._stop.is_set():
            self._on_error(message)

    def stop(self) -> None:
        self._stop.set()
        pipeline, self.pipeline = self.pipeline, None
        if pipeline is not None:
            try:
                pipeline.stop()
            except Exception:
                pass
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=2.0)
        self._thread = None
        self.running = False
        self._align = self._spatial = self._temporal = self._hole = None
        self.profile = self.device = self._depth_sensor = self._color_sensor = None

    def latest_frame(self) -> FramePacket | None:
        with self._frame_lock:
            return self._latest

    def measure(
        self,
        pixel_x: int,
        pixel_y: int,
        packet: FramePacket | None = None,
        search_radius: int = 0,
    ) -> Measurement:
        packet = packet or self.latest_frame()
        if packet is None or packet.depth_raw is None:
            raise CameraError("当前没有可用于测距的深度帧")
        height, width = packet.depth_raw.shape[:2]
        x = min(max(int(pixel_x), 0), width - 1)
        y = min(max(int(pixel_y), 0), height - 1)
        if packet.depth_raw[y, x] == 0 and search_radius > 0 and np is not None:
            radius = max(0, int(search_radius))
            x0, x1 = max(0, x - radius), min(width, x + radius + 1)
            y0, y1 = max(0, y - radius), min(height, y + radius + 1)
            candidates = np.argwhere(packet.depth_raw[y0:y1, x0:x1] > 0)
            if candidates.size:
                candidate_y = candidates[:, 0] + y0
                candidate_x = candidates[:, 1] + x0
                nearest = int(np.argmin((candidate_x - x) ** 2 + (candidate_y - y) ** 2))
                x, y = int(candidate_x[nearest]), int(candidate_y[nearest])
        distance = float(packet.depth_raw[y, x]) * packet.depth_scale
        if distance <= 0:
            raise CameraError(f"像素 ({x}, {y}) 没有有效深度值")
        if rs is not None and packet.intrinsics is not None:
            point = rs.rs2_deproject_pixel_to_point(packet.intrinsics, [x, y], distance)
            px, py, pz = (float(value) for value in point)
        else:
            fx = width * 0.92
            fy = height * 1.22
            px = (x - width / 2.0) * distance / fx
            py = (y - height / 2.0) * distance / fy
            pz = distance
        return Measurement(x, y, distance, px, py, pz)

    def control_state(self) -> dict[str, Any]:
        if self.simulate:
            return {
                "auto_exposure": True,
                "exposure": 8500.0,
                "gain": 16.0,
                "emitter": True,
                "emitter_supported": True,
                "laser_power": 150.0,
                "exposure_range": (1.0, 200000.0),
                "gain_range": (16.0, 248.0),
                "laser_range": (0.0, 360.0),
            }
        if rs is None or not self.running:
            return {}

        def option(sensor: Any, key: Any, default: Any = None) -> Any:
            try:
                return sensor.get_option(key) if sensor and sensor.supports(key) else default
            except Exception:
                return default

        def value_range(sensor: Any, key: Any) -> tuple[float, float] | None:
            try:
                value = sensor.get_option_range(key)
                return float(value.min), float(value.max)
            except Exception:
                return None

        exposure_sensor = self._color_sensor or self._depth_sensor
        return {
            "auto_exposure": bool(option(exposure_sensor, rs.option.enable_auto_exposure, 1)),
            "exposure": option(exposure_sensor, rs.option.exposure),
            "gain": option(exposure_sensor, rs.option.gain),
            "emitter": bool(option(self._depth_sensor, rs.option.emitter_enabled, 0)),
            "emitter_supported": bool(
                self._depth_sensor and self._depth_sensor.supports(rs.option.emitter_enabled)
            ),
            "laser_power": option(self._depth_sensor, rs.option.laser_power),
            "exposure_range": value_range(exposure_sensor, rs.option.exposure),
            "gain_range": value_range(exposure_sensor, rs.option.gain),
            "laser_range": value_range(self._depth_sensor, rs.option.laser_power),
        }

    def apply_controls(
        self,
        auto_exposure: bool,
        exposure: float,
        gain: float,
        emitter: bool,
        laser_power: float,
    ) -> None:
        if not self.running:
            raise CameraError("请先启动相机")
        if self.simulate:
            return
        exposure_sensor = self._color_sensor or self._depth_sensor
        values = (
            (exposure_sensor, rs.option.enable_auto_exposure, 1.0 if auto_exposure else 0.0, "自动曝光"),
            (self._depth_sensor, rs.option.emitter_enabled, 1.0 if emitter else 0.0, "红外发射器"),
            (self._depth_sensor, rs.option.laser_power, laser_power, "激光功率"),
        )
        if not auto_exposure:
            values += (
                (exposure_sensor, rs.option.exposure, exposure, "曝光"),
                (exposure_sensor, rs.option.gain, gain, "增益"),
            )
        changed = 0
        for sensor, key, value, label in values:
            if sensor is None:
                continue
            try:
                if sensor.supports(key):
                    option_range = sensor.get_option_range(key)
                    if value < option_range.min or value > option_range.max:
                        raise CameraError(
                            f"{label} {value:g} 超出设备范围 {option_range.min:g}–{option_range.max:g}"
                        )
                    sensor.set_option(key, float(value))
                    changed += 1
            except CameraError:
                raise
            except Exception as exc:
                raise CameraError(f"设置{label}失败：{exc}") from exc
        if changed == 0:
            raise CameraError("当前设备不支持所选控制项")

    def export_ply(self, path: str) -> None:
        packet = self.latest_frame()
        if packet is None or packet.depth_raw is None:
            raise CameraError("没有可导出的深度帧")
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not self.simulate and rs is not None:
            with self._frame_lock:
                frames = self._latest_frameset
            if frames is None:
                raise CameraError("没有可导出的 RealSense 帧集")
            try:
                ply = rs.save_to_ply(str(destination))
                ply.set_option(rs.save_to_ply.option_ply_binary, True)
                ply.set_option(rs.save_to_ply.option_ply_normals, True)
                ply.process(frames)
                return
            except Exception as exc:
                raise CameraError(f"SDK 导出 PLY 失败：{exc}") from exc
        self._export_simulation_ply(destination, packet)

    @staticmethod
    def _export_simulation_ply(destination: Path, packet: FramePacket) -> None:
        depth = packet.depth_raw
        color = packet.color_rgb
        height, width = depth.shape
        step = max(1, int(math.sqrt(width * height / 80000)))
        vertices: list[tuple[float, float, float, int, int, int]] = []
        fx, fy = width * 0.92, height * 1.22
        for y in range(0, height, step):
            for x in range(0, width, step):
                z = float(depth[y, x]) * packet.depth_scale
                if z <= 0:
                    continue
                px = (x - width / 2.0) * z / fx
                py = (y - height / 2.0) * z / fy
                rgb = color[y, x] if color is not None else (180, 180, 180)
                vertices.append((px, -py, z, int(rgb[0]), int(rgb[1]), int(rgb[2])))
        with destination.open("w", encoding="ascii", newline="\n") as handle:
            handle.write("ply\nformat ascii 1.0\n")
            handle.write(f"element vertex {len(vertices)}\n")
            handle.write("property float x\nproperty float y\nproperty float z\n")
            handle.write("property uchar red\nproperty uchar green\nproperty uchar blue\nend_header\n")
            for vertex in vertices:
                handle.write(
                    f"{vertex[0]:.6f} {vertex[1]:.6f} {vertex[2]:.6f} "
                    f"{vertex[3]} {vertex[4]} {vertex[5]}\n"
                )

    def metadata(self) -> dict[str, Any]:
        packet = self.latest_frame()
        return {
            "device": asdict(self.device_info) if self.device_info else None,
            "settings": asdict(self.settings),
            "record_path": self.record_path,
            "frame": {
                "number": packet.frame_number,
                "hardware_timestamp_ms": packet.timestamp_ms,
                "host_timestamp": packet.received_at,
                "depth_scale_m": packet.depth_scale,
                "measured_fps": packet.fps,
            }
            if packet
            else None,
        }

    def save_metadata(self, path: str) -> None:
        Path(path).write_text(json.dumps(self.metadata(), ensure_ascii=False, indent=2), encoding="utf-8")

    def __enter__(self) -> "DepthCameraController":
        return self

    def __exit__(self, *_: Any) -> None:
        self.stop()
