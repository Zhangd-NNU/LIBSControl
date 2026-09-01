from __future__ import annotations

import ctypes
import math
import random
import re
import socket
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .processing import Spectrum


class SpectrometerError(RuntimeError):
    pass


ERROR_NAMES = {
    0: "成功",
    1: "未知错误",
    2: "未找到设备",
    3: "关闭失败",
    4: "功能未实现",
    5: "设备不支持此功能",
    6: "数据传输错误",
    7: "无效缓冲区",
    8: "输入超出范围",
    9: "光谱仪饱和",
    10: "等待超时",
    11: "缺少回调函数",
    12: "重复请求",
    13: "设备处于特殊模式",
}


@dataclass(frozen=True)
class DeviceInfo:
    channel: int
    sdk_index: int
    model: str
    serial: str
    pixels: int
    wavelength_min: float
    wavelength_max: float
    min_integration_us: int
    max_integration_us: int
    maximum_intensity: int = 65535
    connection: str = "USB"


@dataclass(frozen=True)
class NetworkDevice:
    model: str
    ip: str
    port: int = 8888
    serial: str = ""


def parse_oceanhood_announcement(payload: bytes, ip: str, port: int = 8888) -> NetworkDevice | None:
    """Parse the vendor broadcast: ``oceanmodule:MODEL,SN:SERIAL``."""
    text = payload.decode("utf-8", errors="ignore").strip("\x00\r\n ")
    match = re.search(r"oceanmodule\s*:\s*([^,;]+).*?SN\s*:\s*([^,;\s]+)", text, re.IGNORECASE)
    if not match:
        return None
    return NetworkDevice(match.group(1).strip(), ip, port, match.group(2).strip())


def discover_network_devices(timeout: float = 1.8, listen_port: int = 8888) -> list[NetworkDevice]:
    """Listen for Maria/Oceanhood UDP announcements on the local instrument LAN."""
    found: dict[tuple[str, str], NetworkDevice] = {}
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.bind(("", listen_port))
        sock.settimeout(0.2)
        deadline = time.monotonic() + max(0.2, timeout)
        while time.monotonic() < deadline:
            try:
                payload, address = sock.recvfrom(2048)
            except socket.timeout:
                continue
            device = parse_oceanhood_announcement(payload, address[0], 8888)
            if device:
                found[(device.ip, device.serial)] = device
    except OSError as exc:
        raise SpectrometerError(f"网络扫描失败（UDP {listen_port}）：{exc}") from exc
    finally:
        sock.close()
    return sorted(found.values(), key=lambda item: (item.ip, item.serial))


class BaseController:
    devices: list[DeviceInfo]
    connected: bool

    def connect_usb(self, expected_channels: int = 4) -> list[DeviceInfo]:
        raise NotImplementedError

    def connect_network(self, devices: Iterable[NetworkDevice]) -> list[DeviceInfo]:
        raise NotImplementedError

    def configure(self, integration_us: int, averages: int, boxcar_width: int, trigger_mode: int, delay_us: float) -> None:
        raise NotImplementedError

    def acquire(self) -> tuple[Spectrum, ...]:
        raise NotImplementedError

    def cancel_external_trigger(self) -> None:
        raise NotImplementedError

    def disconnect(self) -> None:
        raise NotImplementedError


class SeaSDKController(BaseController):
    """ctypes binding for the vendor SeaSDK 1.2 C interface."""

    def __init__(self, dll_path: str | Path):
        self.dll_path = Path(dll_path).resolve()
        if not self.dll_path.is_file():
            raise SpectrometerError(f"找不到 SeaSDK DLL：{self.dll_path}")
        try:
            self.lib = ctypes.CDLL(str(self.dll_path))
        except OSError as exc:
            raise SpectrometerError(f"SeaSDK.dll 加载失败（请使用 64 位 Python 并安装设备驱动）：{exc}") from exc
        self._lock = threading.RLock()
        self.devices: list[DeviceInfo] = []
        self._wavelengths: dict[int, tuple[float, ...]] = {}
        self.connected = False
        self._bind()

    def _bind(self) -> None:
        c_int_p = ctypes.POINTER(ctypes.c_int)
        self._signature("seasdk_open_spectrometer", [ctypes.c_int, c_int_p], ctypes.c_int)
        self._signature("seasdk_close_spectrometer", [ctypes.c_int, c_int_p], ctypes.c_int)
        self._signature("seasdk_close_all_spectrometers", [c_int_p], None)
        self._signature("seasdk_add_TCPIPv4_device", [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_int], ctypes.c_int)
        self._signature("seasdk_get_model", [ctypes.c_int, c_int_p, ctypes.c_char_p, ctypes.c_int], ctypes.c_int)
        self._signature("seasdk_get_serial_number", [ctypes.c_int, c_int_p, ctypes.c_char_p, ctypes.c_int], ctypes.c_int)
        self._signature("seasdk_get_formatted_spectrum_length", [ctypes.c_int, c_int_p], ctypes.c_int)
        self._signature("seasdk_get_wavelengths", [ctypes.c_int, c_int_p, ctypes.POINTER(ctypes.c_double), ctypes.c_int], ctypes.c_int)
        self._signature("seasdk_get_formatted_spectrum", [ctypes.c_int, c_int_p, ctypes.POINTER(ctypes.c_double), ctypes.c_int], ctypes.c_int)
        self._signature("seasdk_get_min_integration_time_microsec", [ctypes.c_int, c_int_p], ctypes.c_long)
        self._signature("seasdk_get_max_integration_time_microsec", [ctypes.c_int, c_int_p], ctypes.c_long)
        self._signature("seasdk_get_maximum_intensity", [ctypes.c_int, c_int_p], ctypes.c_int)
        self._signature("seasdk_set_integration_time_microsec_with_confirm", [ctypes.c_int, c_int_p, ctypes.c_ulong], None)
        self._signature("seasdk_set_average", [ctypes.c_int, c_int_p, ctypes.c_ushort], None)
        self._signature("seasdk_set_boxcar", [ctypes.c_int, c_int_p, ctypes.c_ushort], None)
        self._signature("seasdk_set_box_car_mode", [ctypes.c_int, c_int_p, ctypes.c_ubyte], None)
        self._signature("seasdk_set_trigger_mode", [ctypes.c_int, c_int_p, ctypes.c_int], None)
        self._signature("seasdk_set_CCD_delay", [ctypes.c_int, c_int_p, ctypes.c_ulong], None)
        self._signature("seasdk_cancel_external_trigger", [ctypes.c_int, c_int_p], ctypes.c_int)

    def _signature(self, name: str, argtypes: list[object], restype: object) -> None:
        try:
            func = getattr(self.lib, name)
        except AttributeError as exc:
            raise SpectrometerError(f"SeaSDK.dll 缺少接口 {name}，请使用资料包中的 SeaSDK 1.2 DLL") from exc
        func.argtypes = argtypes
        func.restype = restype

    def _error(self, code: int, operation: str) -> SpectrometerError:
        detail = ERROR_NAMES.get(code, f"错误码 {code}")
        return SpectrometerError(f"{operation}失败：{detail} ({code})")

    def _text(self, function: str, index: int) -> str:
        error = ctypes.c_int(0)
        buffer = ctypes.create_string_buffer(128)
        getattr(self.lib, function)(index, ctypes.byref(error), buffer, len(buffer))
        if error.value:
            raise self._error(error.value, function)
        raw = buffer.value
        for encoding in ("utf-8", "gbk", "latin1"):
            try:
                return raw.decode(encoding).strip()
            except UnicodeDecodeError:
                continue
        return raw.decode("utf-8", errors="replace").strip()

    def _read_device(self, channel: int, index: int, connection: str) -> DeviceInfo:
        error = ctypes.c_int(0)
        pixels = self.lib.seasdk_get_formatted_spectrum_length(index, ctypes.byref(error))
        if error.value or pixels <= 0:
            raise self._error(error.value or 7, f"读取通道 {channel} 像素数")
        buffer = (ctypes.c_double * pixels)()
        count = self.lib.seasdk_get_wavelengths(index, ctypes.byref(error), buffer, pixels)
        if error.value or count <= 0:
            raise self._error(error.value or 7, f"读取通道 {channel} 波长")
        wavelengths = tuple(float(buffer[i]) for i in range(min(count, pixels)))
        self._wavelengths[index] = wavelengths
        minimum = int(self.lib.seasdk_get_min_integration_time_microsec(index, ctypes.byref(error)))
        if error.value:
            minimum, error.value = 6, 0
        maximum = int(self.lib.seasdk_get_max_integration_time_microsec(index, ctypes.byref(error)))
        if error.value or maximum <= 0:
            maximum, error.value = 65_535_000, 0
        max_intensity = int(self.lib.seasdk_get_maximum_intensity(index, ctypes.byref(error)))
        if error.value or max_intensity <= 0:
            max_intensity, error.value = 65535, 0
        return DeviceInfo(
            channel=channel,
            sdk_index=index,
            model=self._text("seasdk_get_model", index),
            serial=self._text("seasdk_get_serial_number", index),
            pixels=len(wavelengths),
            wavelength_min=min(wavelengths),
            wavelength_max=max(wavelengths),
            min_integration_us=max(1, minimum),
            max_integration_us=maximum,
            maximum_intensity=max_intensity,
            connection=connection,
        )

    def connect_usb(self, expected_channels: int = 4) -> list[DeviceInfo]:
        with self._lock:
            self.disconnect()
            opened: list[DeviceInfo] = []
            for index in range(expected_channels):
                error = ctypes.c_int(0)
                result = self.lib.seasdk_open_spectrometer(index, ctypes.byref(error))
                if result != 0 or error.value:
                    if index == 0:
                        raise self._error(error.value or 2, "打开 USB 光谱仪")
                    break
                try:
                    opened.append(self._read_device(len(opened) + 1, index, "USB"))
                except Exception:
                    self.lib.seasdk_close_spectrometer(index, ctypes.byref(error))
                    raise
            self.devices = opened
            self.connected = bool(opened)
            return list(opened)

    def connect_network(self, devices: Iterable[NetworkDevice]) -> list[DeviceInfo]:
        with self._lock:
            self.disconnect()
            opened: list[DeviceInfo] = []
            for item in devices:
                index = self.lib.seasdk_add_TCPIPv4_device(item.model.encode(), item.ip.encode(), item.port)
                if index < 32:
                    self.disconnect()
                    raise SpectrometerError(f"添加网络设备失败：{item.model} {item.ip}:{item.port}")
                error = ctypes.c_int(0)
                result = self.lib.seasdk_open_spectrometer(index, ctypes.byref(error))
                if result != 0 or error.value:
                    self.disconnect()
                    raise self._error(error.value or 2, f"打开网络设备 {item.ip}")
                opened.append(self._read_device(len(opened) + 1, index, f"{item.ip}:{item.port}"))
            if not opened:
                raise SpectrometerError("没有可连接的网络光谱仪")
            self.devices = opened
            self.connected = True
            return list(opened)

    def configure(self, integration_us: int, averages: int, boxcar_width: int, trigger_mode: int, delay_us: float) -> None:
        if not self.connected:
            raise SpectrometerError("光谱仪尚未连接")
        if averages < 1 or averages > 65535:
            raise SpectrometerError("平均次数应为 1~65535")
        if boxcar_width < 0 or boxcar_width > 65535 or (boxcar_width > 0 and boxcar_width % 2 == 0):
            raise SpectrometerError("硬件像素平滑必须为 0 或正奇数")
        with self._lock:
            for device in self.devices:
                if not device.min_integration_us <= integration_us <= device.max_integration_us:
                    raise SpectrometerError(
                        f"通道 {device.channel} 积分时间范围为 {device.min_integration_us}~{device.max_integration_us} μs"
                    )
                error = ctypes.c_int(0)
                index = device.sdk_index
                self.lib.seasdk_set_integration_time_microsec_with_confirm(index, ctypes.byref(error), integration_us)
                if error.value:
                    raise self._error(error.value, f"设置通道 {device.channel} 积分时间")
                self.lib.seasdk_set_average(index, ctypes.byref(error), averages)
                if error.value:
                    raise self._error(error.value, f"设置通道 {device.channel} 平均次数")
                self.lib.seasdk_set_box_car_mode(index, ctypes.byref(error), 1 if boxcar_width else 0)
                if not error.value and boxcar_width:
                    self.lib.seasdk_set_boxcar(index, ctypes.byref(error), boxcar_width)
                if error.value:
                    raise self._error(error.value, f"设置通道 {device.channel} 像素平滑")
                self.lib.seasdk_set_CCD_delay(index, ctypes.byref(error), int(round(delay_us * 100.0)))
                if error.value:
                    raise self._error(error.value, f"设置通道 {device.channel} 采集延迟")
                self.lib.seasdk_set_trigger_mode(index, ctypes.byref(error), trigger_mode)
                if error.value:
                    raise self._error(error.value, f"设置通道 {device.channel} 触发模式")

    def acquire(self) -> tuple[Spectrum, ...]:
        if not self.connected:
            raise SpectrometerError("光谱仪尚未连接")
        spectra: list[Spectrum] = []
        with self._lock:
            for device in self.devices:
                error = ctypes.c_int(0)
                values = (ctypes.c_double * device.pixels)()
                count = self.lib.seasdk_get_formatted_spectrum(
                    device.sdk_index, ctypes.byref(error), values, device.pixels
                )
                if error.value or count <= 0:
                    raise self._error(error.value or 7, f"采集通道 {device.channel}")
                wavelengths = self._wavelengths[device.sdk_index][:count]
                spectra.append(
                    Spectrum(
                        device.channel,
                        wavelengths,
                        tuple(float(values[i]) for i in range(count)),
                        device.model,
                        device.serial,
                    )
                )
        return tuple(spectra)

    def cancel_external_trigger(self) -> None:
        # The cancel API exists specifically to interrupt a blocking external-trigger read.
        # It intentionally does not take _lock because acquire() may currently hold it.
        for device in list(self.devices):
            error = ctypes.c_int(0)
            try:
                self.lib.seasdk_cancel_external_trigger(device.sdk_index, ctypes.byref(error))
            except (OSError, ValueError):
                pass

    def disconnect(self) -> None:
        if not hasattr(self, "lib"):
            return
        error = ctypes.c_int(0)
        try:
            self.lib.seasdk_close_all_spectrometers(ctypes.byref(error))
        except (OSError, ValueError):
            pass
        self.devices = []
        self._wavelengths = {}
        self.connected = False


class SimulationController(BaseController):
    """Four-channel synthetic Maria device for development and operator training."""

    RANGES = ((190.0, 350.0), (330.0, 510.0), (490.0, 680.0), (650.0, 900.0))

    def __init__(self, seed: int = 240510):
        self.devices: list[DeviceInfo] = []
        self.connected = False
        self.integration_us = 1000
        self.averages = 1
        self.boxcar_width = 0
        self.trigger_mode = 0
        self.delay_us = 0.0
        self._rng = random.Random(seed)
        self._cancelled = threading.Event()
        self._frame = 0

    def _connect(self, connection: str) -> list[DeviceInfo]:
        self.devices = [
            DeviceInfo(i, i - 1, "Maria-SIM", f"SIM-{i:02d}", 1024, start, end, 6, 65_535_000, 65535, connection)
            for i, (start, end) in enumerate(self.RANGES, 1)
        ]
        self.connected = True
        return list(self.devices)

    def connect_usb(self, expected_channels: int = 4) -> list[DeviceInfo]:
        return self._connect("SIM / USB")[:expected_channels]

    def connect_network(self, devices: Iterable[NetworkDevice]) -> list[DeviceInfo]:
        return self._connect("SIM / Ethernet")

    def configure(self, integration_us: int, averages: int, boxcar_width: int, trigger_mode: int, delay_us: float) -> None:
        if not self.connected:
            raise SpectrometerError("光谱仪尚未连接")
        if integration_us < 6:
            raise SpectrometerError("Maria 最短积分时间为 6 μs")
        if averages < 1:
            raise SpectrometerError("平均次数至少为 1")
        if boxcar_width and boxcar_width % 2 == 0:
            raise SpectrometerError("硬件像素平滑必须为 0 或正奇数")
        self.integration_us, self.averages = integration_us, averages
        self.boxcar_width, self.trigger_mode, self.delay_us = boxcar_width, trigger_mode, delay_us
        self._cancelled.clear()

    def acquire(self) -> tuple[Spectrum, ...]:
        if not self.connected:
            raise SpectrometerError("光谱仪尚未连接")
        if self.trigger_mode in (2, 6):
            self._cancelled.wait(0.45)
            if self._cancelled.is_set():
                self._cancelled.clear()
                raise SpectrometerError("外触发等待已取消")
        else:
            time.sleep(min(0.12, max(0.006, self.integration_us / 1_000_000.0)))
        self._frame += 1
        amplitude = min(1.0, self.integration_us / 5000.0) * 52000.0
        peaks = ((248.33, 0.25, 0.45), (324.75, 0.18, 0.72), (393.37, 0.22, 0.92),
                 (422.67, 0.28, 0.50), (589.00, 0.30, 0.82), (656.28, 0.35, 0.65), (777.19, 0.42, 0.72))
        spectra: list[Spectrum] = []
        drift = math.sin(self._frame / 11.0) * 0.012
        for device in self.devices:
            step = (device.wavelength_max - device.wavelength_min) / (device.pixels - 1)
            wavelengths = tuple(device.wavelength_min + step * i for i in range(device.pixels))
            values: list[float] = []
            channel_gain = 1.0 + (device.channel - 2.5) * 0.045
            for wavelength in wavelengths:
                intensity = 180.0 + amplitude * 0.014 * (1.0 + math.sin(wavelength / 29.0))
                for center, width, strength in peaks:
                    intensity += amplitude * strength * math.exp(-0.5 * ((wavelength - center) / width) ** 2)
                noise = self._rng.gauss(0.0, 80.0 / math.sqrt(max(1, self.averages)))
                values.append(max(0.0, min(65535.0, (intensity + noise) * channel_gain * (1 + drift))))
            spectra.append(Spectrum(device.channel, wavelengths, tuple(values), device.model, device.serial))
        return tuple(spectra)

    def cancel_external_trigger(self) -> None:
        self._cancelled.set()

    def disconnect(self) -> None:
        self.cancel_external_trigger()
        self.devices = []
        self.connected = False
