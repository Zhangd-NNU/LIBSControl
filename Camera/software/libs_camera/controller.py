from __future__ import annotations

import csv
import ctypes
import importlib
import platform
import queue
import re
import threading
import time
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from PIL import Image, ImageDraw

from .models import (
    MONITOR,
    ROLE_NAMES,
    ROLES,
    TRIGGER_A,
    TRIGGER_B,
    CameraDescriptor,
    CameraSettings,
    CameraStats,
    FrameMetadata,
    SaveSettings,
)


class CameraError(RuntimeError):
    pass


EventSink = Callable[[dict[str, Any]], None]


def _safe_name(value: str) -> str:
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value.strip())
    value = value.rstrip(". ")
    return value[:80] or f"capture_{datetime.now():%Y%m%d_%H%M%S}"


def _safe_file_stem(value: str) -> str:
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value.strip())
    value = value.rstrip(". ")
    return value[:80] or "image"


def _unique_directory(parent: Path, requested_name: str) -> Path:
    parent.mkdir(parents=True, exist_ok=True)
    base = parent / _safe_name(requested_name)
    candidate = base
    index = 2
    while candidate.exists():
        candidate = parent / f"{base.name}_{index}"
        index += 1
    candidate.mkdir(parents=False)
    return candidate


def _status_message(sdk: Any, code: int) -> str:
    try:
        return sdk.CameraGetErrorString(code)
    except Exception:
        return f"SDK 错误码 {code}"


def _check(sdk: Any, code: int, action: str) -> None:
    if code != sdk.CAMERA_STATUS_SUCCESS:
        raise CameraError(f"{action}失败：{_status_message(sdk, code)} ({code})")


class CameraEndpoint:
    """一个相机角色的生命周期、采集线程和保存队列。"""

    SAVE_QUEUE_SIZE = 256

    def __init__(
        self,
        descriptor: CameraDescriptor,
        raw_device: Any,
        role: str,
        settings: CameraSettings,
        sdk: Any | None,
        event_sink: EventSink,
    ):
        self.descriptor = descriptor
        self.raw_device = raw_device
        self.role = role
        self.settings = settings
        self.sdk = sdk
        self.event_sink = event_sink
        self.simulated = descriptor.simulated
        self.handle = 0
        self.capability: Any | None = None
        self.frame_buffer = 0
        self.mono = False
        self.connected = False
        self.capture_stop = threading.Event()
        self.capture_thread: threading.Thread | None = None
        self.save_queue: queue.Queue[Any] | None = None
        self.save_thread: threading.Thread | None = None
        self.save_directory: Path | None = None
        self.manifest_path: Path | None = None
        self.save_settings: SaveSettings | None = None
        self.simulated_triggers: queue.Queue[None] = queue.Queue(maxsize=1000)
        self.stats = CameraStats()
        self._sdk_lock = threading.RLock()
        self._state_lock = threading.RLock()
        self._last_preview = 0.0
        self._last_stats_emit = 0.0
        self._last_drop_log = 0.0
        self._sequence = 0

    @property
    def is_external(self) -> bool:
        return self.role in (TRIGGER_A, TRIGGER_B)

    def emit(self, event_type: str, **payload: Any) -> None:
        self.event_sink({"type": event_type, "role": self.role, **payload})

    def connect(self) -> None:
        self.settings.validate(self.is_external)
        if self.connected:
            return
        if self.simulated:
            self.connected = True
            self.emit("log", level="info", message=f"{ROLE_NAMES[self.role]} 已连接（仿真）")
            if self.role == MONITOR:
                self.start_monitor()
            return
        if self.sdk is None:
            raise CameraError("相机 SDK 未加载")
        try:
            self.handle = self.sdk.CameraInit(self.raw_device, -1, -1)
            self.capability = self.sdk.CameraGetCapability(self.handle)
            if self.is_external and self.capability.iTriggerDesc <= 2:
                raise CameraError(f"{self.descriptor.display_name} 不支持硬件外触发模式")
            self.mono = bool(self.capability.sIspCapacity.bMonoSensor)
            output_format = self.sdk.CAMERA_MEDIA_TYPE_MONO8 if self.mono else self.sdk.CAMERA_MEDIA_TYPE_RGB8
            _check(self.sdk, self.sdk.CameraSetIspOutFormat(self.handle, output_format), "设置 ISP 输出格式")
            max_width = self.capability.sResolutionRange.iWidthMax
            max_height = self.capability.sResolutionRange.iHeightMax
            channels = 1 if self.mono else 3
            self.frame_buffer = self.sdk.CameraAlignMalloc(max_width * max_height * channels, 16)
            if not self.frame_buffer:
                raise CameraError("无法分配相机图像缓冲区")
            self._apply_settings_locked(self.settings)
            _check(self.sdk, self.sdk.CameraPlay(self.handle), "启动相机取流")
            self.sdk.CameraClearBuffer(self.handle)
            self.connected = True
            self.emit(
                "log",
                level="info",
                message=(f"{ROLE_NAMES[self.role]} 已连接：{self.descriptor.friendly_name} "
                         f"SN {self.descriptor.serial}"),
            )
            if self.role == MONITOR:
                self.start_monitor()
        except Exception:
            self._release_sdk()
            raise

    def apply_settings(self, settings: CameraSettings) -> None:
        settings.validate(self.is_external)
        with self._sdk_lock:
            if self.connected and not self.simulated:
                self._apply_settings_locked(settings)
            self.settings = replace(settings)
        self.emit("log", level="info", message=f"{ROLE_NAMES[self.role]} 参数已应用")

    def _apply_settings_locked(self, settings: CameraSettings) -> None:
        assert self.sdk is not None
        mode = 2 if self.is_external else 0
        _check(self.sdk, self.sdk.CameraSetTriggerMode(self.handle, mode), "设置触发模式")
        _check(self.sdk, self.sdk.CameraSetAeState(self.handle, int(settings.auto_exposure)), "设置自动曝光")
        if not settings.auto_exposure:
            _check(
                self.sdk,
                self.sdk.CameraSetExposureTime(self.handle, settings.exposure_ms * 1000.0),
                "设置曝光时间",
            )
            _check(self.sdk, self.sdk.CameraSetAnalogGain(self.handle, settings.analog_gain), "设置模拟增益")
        if self.is_external:
            _check(self.sdk, self.sdk.CameraSetTriggerCount(self.handle, 1), "设置单次触发帧数")
            _check(
                self.sdk,
                self.sdk.CameraSetExtTrigSignalType(self.handle, settings.trigger_edge),
                "设置外触发信号类型",
            )
            _check(
                self.sdk,
                self.sdk.CameraSetExtTrigDelayTime(self.handle, settings.trigger_delay_us),
                "设置外触发延时",
            )
            _check(
                self.sdk,
                self.sdk.CameraSetExtTrigJitterTime(self.handle, settings.trigger_jitter_us),
                "设置外触发去抖",
            )

    def start_monitor(self) -> None:
        if self.role != MONITOR or not self.connected:
            return
        if self.capture_thread and self.capture_thread.is_alive():
            return
        self.capture_stop.clear()
        self.capture_thread = threading.Thread(
            target=self._capture_loop,
            name=f"camera-{self.role}",
            daemon=True,
        )
        self.capture_thread.start()

    def start_trigger_capture(self, session_root: Path | None, save_settings: SaveSettings) -> None:
        if not self.is_external:
            raise CameraError("监控相机不能启动外触发保存")
        if not self.connected:
            raise CameraError(f"{ROLE_NAMES[self.role]} 尚未连接")
        if self.capture_thread and self.capture_thread.is_alive():
            raise CameraError(f"{ROLE_NAMES[self.role]} 已在采集")
        self.save_settings = save_settings
        self.stats = CameraStats()
        self._sequence = 0
        self._last_preview = 0.0
        self._last_stats_emit = 0.0
        self._last_drop_log = 0.0
        self.save_directory = None
        self.manifest_path = None
        self.save_queue = None
        self.save_thread = None
        if save_settings.save_enabled:
            if session_root is None:
                raise CameraError("保存已启用，但没有有效的会话目录")
            role_folder = "Camera A" if self.role == TRIGGER_A else "Camera B"
            self.save_directory = session_root / role_folder
            self.save_directory.mkdir(parents=True, exist_ok=True)
            self.manifest_path = self.save_directory / "manifest.csv"
            self.save_queue = queue.Queue(maxsize=self.SAVE_QUEUE_SIZE)
            with self.manifest_path.open("w", newline="", encoding="utf-8-sig") as stream:
                csv.writer(stream).writerow([
                    "sequence", "local_time", "sdk_timestamp_100us", "width", "height",
                    "exposure_us", "analog_gain", "is_trigger", "serial", "filename",
                ])
            self.save_thread = threading.Thread(target=self._save_loop, name=f"save-{self.role}", daemon=True)
            self.save_thread.start()
        if not self.simulated:
            with self._sdk_lock:
                self.sdk.CameraClearBuffer(self.handle)
        self.capture_stop.clear()
        self.capture_thread = threading.Thread(
            target=self._capture_loop,
            name=f"camera-{self.role}",
            daemon=True,
        )
        self.capture_thread.start()
        mode_text = "保存图片" if save_settings.save_enabled else "仅采集预览，不保存"
        self.emit("log", level="info", message=f"{ROLE_NAMES[self.role]} 已进入外触发等待（{mode_text}）")

    def stop_trigger_capture(self) -> None:
        if not self.is_external:
            return
        self.capture_stop.set()
        thread = self.capture_thread
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=2.0)
        self.capture_thread = None
        save_queue = self.save_queue
        if save_queue is not None:
            # 采集线程停止后不会再入队；先等待已有帧全部落盘，再结束保存线程。
            save_queue.join()
            save_queue.put(None)
        thread = self.save_thread
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=10.0)
        self.save_thread = None
        self.save_queue = None
        self.emit("stats", stats=self.stats.snapshot())

    def software_trigger(self) -> None:
        if not self.is_external or not self.connected:
            raise CameraError(f"{ROLE_NAMES[self.role]} 未连接或不是触发相机")
        if not self.capture_thread or not self.capture_thread.is_alive():
            raise CameraError(f"{ROLE_NAMES[self.role]} 尚未开始采集")
        if self.simulated:
            try:
                self.simulated_triggers.put_nowait(None)
            except queue.Full as exc:
                raise CameraError("仿真触发队列已满") from exc
        else:
            with self._sdk_lock:
                _check(self.sdk, self.sdk.CameraSoftTrigger(self.handle), "发送测试触发")

    def _capture_loop(self) -> None:
        if self.simulated:
            self._simulation_capture_loop()
            return
        consecutive_errors = 0
        while not self.capture_stop.is_set():
            raw = 0
            try:
                # CameraGetImageBuffer 在外触发等待时最多阻塞 200 ms。不能在此
                # 持有参数/命令锁，否则测试触发和参数应用可能长期饥饿。
                # 断开流程会先停止并 join 本线程，再释放句柄和帧缓冲区。
                try:
                    raw, head = self.sdk.CameraGetImageBuffer(self.handle, 200)
                except self.sdk.CameraException as exc:
                    if exc.error_code == self.sdk.CAMERA_STATUS_TIME_OUT:
                        continue
                    raise
                try:
                    _check(
                        self.sdk,
                        self.sdk.CameraImageProcess(self.handle, raw, self.frame_buffer, head),
                        "图像 ISP 处理",
                    )
                    channels = 1 if self.mono else 3
                    byte_count = head.iWidth * head.iHeight * channels
                    data = bytes((ctypes.c_ubyte * byte_count).from_address(self.frame_buffer))
                finally:
                    self.sdk.CameraReleaseImageBuffer(self.handle, raw)
                    raw = 0
                image_mode = "L" if self.mono else "RGB"
                image = Image.frombytes(
                    image_mode,
                    (head.iWidth, head.iHeight),
                    data,
                    "raw",
                    image_mode,
                    0,
                    -1 if platform.system() == "Windows" else 1,
                )
                metadata = self._metadata_from_head(head)
                self._handle_frame(image, metadata)
                consecutive_errors = 0
            except Exception as exc:
                if self.capture_stop.is_set():
                    break
                consecutive_errors += 1
                self.stats.errors += 1
                self.emit("log", level="error", message=f"{ROLE_NAMES[self.role]} 取帧错误：{exc}")
                if consecutive_errors >= 5:
                    self.emit("camera_fault", message=f"{ROLE_NAMES[self.role]} 连续取帧失败，采集线程已停止")
                    break
                time.sleep(0.1)

    def _metadata_from_head(self, head: Any) -> FrameMetadata:
        self._sequence += 1
        return FrameMetadata(
            role=self.role,
            serial=self.descriptor.serial,
            sequence=self._sequence,
            local_time=datetime.now().astimezone().isoformat(timespec="milliseconds"),
            sdk_timestamp_100us=int(head.uiTimeStamp),
            width=int(head.iWidth),
            height=int(head.iHeight),
            exposure_us=int(head.uiExpTime),
            analog_gain=float(head.fAnalogGain),
            is_trigger=int(head.bIsTrigger),
        )

    def _simulation_capture_loop(self) -> None:
        if self.role == MONITOR:
            period = 1.0 / 15.0
            while not self.capture_stop.wait(period):
                self._sequence += 1
                image = self._simulation_image(self._sequence)
                metadata = self._simulation_metadata(image, self._sequence, 0)
                self._handle_frame(image, metadata)
            return
        while not self.capture_stop.is_set():
            try:
                self.simulated_triggers.get(timeout=0.2)
            except queue.Empty:
                continue
            self._sequence += 1
            image = self._simulation_image(self._sequence)
            metadata = self._simulation_metadata(image, self._sequence, 1)
            self._handle_frame(image, metadata)

    def _simulation_image(self, sequence: int) -> Image.Image:
        width, height = (1280, 720) if self.role == MONITOR else (1024, 768)
        role_color = {
            TRIGGER_A: (0, 180, 220),
            TRIGGER_B: (0, 220, 155),
            MONITOR: (50, 150, 255),
        }[self.role]
        image = Image.new("RGB", (width, height), (4, 8, 12))
        draw = ImageDraw.Draw(image)
        for x in range(0, width, 64):
            shade = 15 + (x // 64) % 2 * 5
            draw.line((x, 0, x, height), fill=(shade, shade + 5, shade + 8))
        for y in range(0, height, 64):
            draw.line((0, y, width, y), fill=(16, 23, 27))
        offset = (sequence * 17) % max(1, width - 200)
        draw.rectangle((offset, height // 2 - 55, offset + 200, height // 2 + 55), outline=role_color, width=5)
        draw.line((width // 2 - 35, height // 2, width // 2 + 35, height // 2), fill=(255, 220, 70), width=2)
        draw.line((width // 2, height // 2 - 35, width // 2, height // 2 + 35), fill=(255, 220, 70), width=2)
        draw.text((32, 28), f"{ROLE_NAMES[self.role]}  SIMULATION", fill=role_color)
        draw.text((32, 54), f"SN {self.descriptor.serial}  FRAME {sequence:06d}", fill=(220, 230, 235))
        draw.text((32, height - 46), datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3], fill=(150, 165, 175))
        return image

    def _simulation_metadata(self, image: Image.Image, sequence: int, is_trigger: int) -> FrameMetadata:
        return FrameMetadata(
            role=self.role,
            serial=self.descriptor.serial,
            sequence=sequence,
            local_time=datetime.now().astimezone().isoformat(timespec="milliseconds"),
            sdk_timestamp_100us=int(time.monotonic() * 10_000) & 0xFFFFFFFF,
            width=image.width,
            height=image.height,
            exposure_us=int(self.settings.exposure_ms * 1000),
            analog_gain=float(self.settings.analog_gain),
            is_trigger=is_trigger,
        )

    def _handle_frame(self, image: Image.Image, metadata: FrameMetadata) -> None:
        self.stats.received += 1
        self.stats.last_frame_time = metadata.local_time
        now = time.monotonic()
        # 实验图像一帧不漏地进入保存队列；界面只需要看到最新状态。
        # 外触发高频运行时限制缩略图为约 6 FPS，避免旧预览挤占 Tk 主线程。
        preview_period = 0.08 if self.role == MONITOR else 0.16
        if now - self._last_preview >= preview_period:
            preview = image if self.role == MONITOR else image.copy()
            preview.thumbnail(
                (1100, 680) if self.role == MONITOR else (520, 300),
                Image.Resampling.BILINEAR,
            )
            self.emit("preview", image=preview, metadata=metadata)
            self._last_preview = now
        if self.is_external and self.save_queue is not None:
            try:
                self.save_queue.put_nowait((image, metadata))
            except queue.Full:
                self.stats.dropped += 1
                if now - self._last_drop_log >= 1.0:
                    self.emit(
                        "log",
                        level="error",
                        message=f"{ROLE_NAMES[self.role]} 保存队列已满，累计丢弃 {self.stats.dropped} 帧",
                    )
                    self._last_drop_log = now
        elif (self.role == MONITOR or self.is_external) and now - self._last_stats_emit >= 0.20:
            self.emit("stats", stats=self.stats.snapshot())
            self._last_stats_emit = now

    def _save_loop(self) -> None:
        assert self.save_queue is not None
        assert self.manifest_path is not None
        # 清单文件在整个会话中只打开一次，避免每帧重复创建文件句柄。
        # 行缓冲保证每条记录及时可见，同时显著减少元数据操作开销。
        with self.manifest_path.open("a", newline="", encoding="utf-8-sig", buffering=1) as manifest_stream:
            manifest_writer = csv.writer(manifest_stream)
            while True:
                item = self.save_queue.get()
                try:
                    if item is None:
                        return
                    image, metadata = item
                    self._save_one(image, metadata, manifest_writer)
                except Exception as exc:
                    self.stats.errors += 1
                    self.emit("log", level="error", message=f"{ROLE_NAMES[self.role]} 保存失败：{exc}")
                finally:
                    self.save_queue.task_done()

    def _save_one(self, image: Image.Image, metadata: FrameMetadata, manifest_writer: Any) -> None:
        assert self.save_directory is not None and self.manifest_path is not None and self.save_settings is not None
        extension = {"PNG": ".png", "JPG": ".jpg", "BMP": ".bmp"}[self.save_settings.image_format]
        file_index = self.stats.saved + 1
        filename = f"{self.save_settings.file_name}-{file_index}{extension}"
        destination = self.save_directory / filename
        options: dict[str, Any] = {}
        if self.save_settings.image_format == "JPG":
            options.update(quality=self.save_settings.jpeg_quality, subsampling=0)
            if image.mode != "RGB":
                image = image.convert("RGB")
        elif self.save_settings.image_format == "PNG":
            # 采集优先：低压缩等级显著降低触发瞬间的 CPU 峰值，仍保持无损。
            options.update(compress_level=1)
        image.save(destination, format="JPEG" if self.save_settings.image_format == "JPG" else self.save_settings.image_format, **options)
        manifest_writer.writerow([
            metadata.sequence,
            metadata.local_time,
            metadata.sdk_timestamp_100us,
            metadata.width,
            metadata.height,
            metadata.exposure_us,
            f"{metadata.analog_gain:.3f}",
            metadata.is_trigger,
            metadata.serial,
            filename,
        ])
        self.stats.saved += 1
        self.stats.last_filename = filename
        now = time.monotonic()
        if now - self._last_stats_emit >= 0.15:
            self.emit("stats", stats=self.stats.snapshot())
            self._last_stats_emit = now

    def disconnect(self) -> None:
        if not self.connected and not self.handle:
            return
        if self.is_external:
            self.stop_trigger_capture()
        else:
            self.capture_stop.set()
            thread = self.capture_thread
            if thread and thread.is_alive() and thread is not threading.current_thread():
                thread.join(timeout=2.0)
            self.capture_thread = None
        self._release_sdk()
        self.connected = False
        self.emit("log", level="info", message=f"{ROLE_NAMES[self.role]} 已断开")

    def _release_sdk(self) -> None:
        if self.simulated:
            return
        if self.sdk is None:
            return
        with self._sdk_lock:
            if self.handle:
                try:
                    self.sdk.CameraStop(self.handle)
                except Exception:
                    pass
                try:
                    self.sdk.CameraUnInit(self.handle)
                except Exception:
                    pass
                self.handle = 0
            if self.frame_buffer:
                try:
                    self.sdk.CameraAlignFree(self.frame_buffer)
                except Exception:
                    pass
                self.frame_buffer = 0


class CameraSystem:
    def __init__(self, event_sink: EventSink | None = None):
        self.event_sink = event_sink or (lambda event: None)
        self.sdk: Any | None = None
        self.descriptors: dict[str, CameraDescriptor] = {}
        self.raw_devices: dict[str, Any] = {}
        self.endpoints: dict[str, CameraEndpoint] = {}
        self.session_directory: Path | None = None
        self.recording = False
        self._lock = threading.RLock()

    def emit(self, event_type: str, **payload: Any) -> None:
        self.event_sink({"type": event_type, **payload})

    def _load_sdk(self) -> Any:
        if self.sdk is not None:
            return self.sdk
        try:
            self.sdk = importlib.import_module("libs_camera.vendor.mvsdk")
            return self.sdk
        except (ImportError, OSError) as exc:
            architecture = platform.architecture()[0]
            dll_name = "MVCAMSDK_X64.dll" if architecture == "64bit" else "MVCAMSDK.dll"
            raise CameraError(
                f"无法加载厂商 SDK（{dll_name}）。请先安装资料目录中的 HuaTengVision Camera Platform，"
                f"并确保 Python 与 SDK 位数一致。原始错误：{exc}"
            ) from exc

    def enumerate_devices(self, simulation: bool = False) -> list[CameraDescriptor]:
        with self._lock:
            if self.endpoints:
                raise CameraError("请先断开已连接的相机，再刷新设备列表")
            self.descriptors.clear()
            self.raw_devices.clear()
            if simulation:
                for index, role in enumerate(ROLES, 1):
                    serial = f"SIM-CAM-{index:02d}"
                    descriptor = CameraDescriptor(
                        uid=serial,
                        serial=serial,
                        friendly_name=f"虚拟工业相机 {index}",
                        product_name="SIM-HV-CAMERA",
                        port_type="SIM",
                        simulated=True,
                    )
                    self.descriptors[descriptor.uid] = descriptor
                    self.raw_devices[descriptor.uid] = None
            else:
                sdk = self._load_sdk()
                for index, device in enumerate(sdk.CameraEnumerateDevice()):
                    serial = device.GetSn().strip() or f"NO-SN-{index + 1}"
                    uid = serial
                    if uid in self.descriptors:
                        uid = f"{serial}#{index + 1}"
                    descriptor = CameraDescriptor(
                        uid=uid,
                        serial=serial,
                        friendly_name=device.GetFriendlyName() or device.GetProductName() or f"相机 {index + 1}",
                        product_name=device.GetProductName(),
                        port_type=device.GetPortType(),
                        simulated=False,
                    )
                    self.descriptors[uid] = descriptor
                    self.raw_devices[uid] = device.clone()
            devices = list(self.descriptors.values())
            self.emit("devices", devices=devices)
            return devices

    def connect(self, assignments: dict[str, str], settings: dict[str, CameraSettings]) -> None:
        with self._lock:
            if self.endpoints:
                raise CameraError("相机已经连接")
            invalid_roles = [role for role in assignments if role not in ROLES]
            if invalid_roles:
                raise CameraError("存在无效的相机角色")
            if not assignments:
                raise CameraError("至少需要分配并连接一台相机")
            selected = list(assignments.values())
            if len(set(selected)) != len(selected):
                raise CameraError("一台相机不能同时分配给多个角色")
            unknown = [uid for uid in selected if uid not in self.descriptors]
            if unknown:
                raise CameraError("设备列表已变化，请重新刷新相机")
            created: dict[str, CameraEndpoint] = {}
            try:
                for role in ROLES:
                    if role not in assignments:
                        continue
                    descriptor = self.descriptors[assignments[role]]
                    endpoint = CameraEndpoint(
                        descriptor,
                        self.raw_devices[assignments[role]],
                        role,
                        settings[role],
                        None if descriptor.simulated else self._load_sdk(),
                        self.event_sink,
                    )
                    endpoint.connect()
                    created[role] = endpoint
                self.endpoints = created
                self.emit("connected", assignments={role: endpoint.descriptor for role, endpoint in self.endpoints.items()})
            except Exception:
                for endpoint in reversed(list(created.values())):
                    endpoint.disconnect()
                raise

    def apply_settings(self, settings: dict[str, CameraSettings]) -> None:
        with self._lock:
            if not self.endpoints:
                raise CameraError("请先连接至少一台相机")
            for role, endpoint in self.endpoints.items():
                endpoint.apply_settings(settings[role])

    def start_recording(self, save_settings: SaveSettings) -> Path | None:
        with self._lock:
            if self.recording:
                raise CameraError("外触发采集已经在运行")
            trigger_roles = [role for role in (TRIGGER_A, TRIGGER_B) if role in self.endpoints]
            if not trigger_roles:
                raise CameraError("当前没有连接触发相机 A 或 B；监控相机可继续实时预览")
            save_settings.validate()
            session: Path | None = None
            if save_settings.save_enabled:
                root = Path(save_settings.output_root).expanduser().resolve()
                folder_name = save_settings.folder_name or f"capture_{datetime.now():%Y%m%d_%H%M%S}"
                save_settings.folder_name = _safe_name(folder_name)
                save_settings.file_name = _safe_file_stem(save_settings.file_name)
                session = _unique_directory(root, save_settings.folder_name)
            started: list[CameraEndpoint] = []
            try:
                for role in trigger_roles:
                    endpoint = self.endpoints[role]
                    endpoint.start_trigger_capture(session, save_settings)
                    started.append(endpoint)
                if session is not None:
                    info_path = session / "session_info.txt"
                    info_path.write_text(
                        "LIBS 三相机采集会话\n"
                        f"开始时间: {datetime.now().astimezone().isoformat(timespec='seconds')}\n"
                        f"文件名: {save_settings.file_name}\n"
                        f"图像格式: {save_settings.image_format}\n"
                        + "\n".join(
                            f"{ROLE_NAMES[role]}: {self.endpoints[role].descriptor.display_name}"
                            for role in ROLES if role in self.endpoints
                        )
                        + "\n",
                        encoding="utf-8",
                    )
                self.session_directory = session
                self.recording = True
                self.emit(
                    "recording_started",
                    path=str(session or ""),
                    save_enabled=save_settings.save_enabled,
                )
                return session
            except Exception:
                for endpoint in started:
                    endpoint.stop_trigger_capture()
                raise

    def stop_recording(self) -> None:
        with self._lock:
            if not self.recording:
                return
            for role in (TRIGGER_A, TRIGGER_B):
                endpoint = self.endpoints.get(role)
                if endpoint:
                    endpoint.stop_trigger_capture()
            self.recording = False
            self.emit("recording_stopped", path=str(self.session_directory or ""))

    def software_trigger(self, role: str | None = None) -> None:
        with self._lock:
            roles = tuple(selected for selected in (TRIGGER_A, TRIGGER_B) if selected in self.endpoints) if role is None else (role,)
            if not roles:
                raise CameraError("当前没有连接任何触发相机")
            for selected_role in roles:
                endpoint = self.endpoints.get(selected_role)
                if endpoint is None:
                    raise CameraError(f"{ROLE_NAMES.get(selected_role, selected_role)} 尚未连接")
                endpoint.software_trigger()

    def stats_snapshot(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            return {role: endpoint.stats.snapshot() for role, endpoint in self.endpoints.items()}

    def disconnect(self) -> None:
        with self._lock:
            self.stop_recording()
            for role in reversed(ROLES):
                endpoint = self.endpoints.get(role)
                if endpoint:
                    endpoint.disconnect()
            self.endpoints.clear()
            self.recording = False
            self.emit("disconnected")

    def close(self) -> None:
        self.disconnect()
