from __future__ import annotations

import threading
import sys
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from .protocol import STOP_FRAME, TimingParameters, build_parameter_frame, frame_hex


class TimingControllerError(RuntimeError):
    pass


class _WindowsSerial:
    """不依赖第三方库的 Windows 8N1 串口后端。"""

    def __init__(self, port: str, baudrate: int):
        import ctypes
        from ctypes import wintypes

        class DCB(ctypes.Structure):
            _fields_ = [
                ("DCBlength", wintypes.DWORD), ("BaudRate", wintypes.DWORD),
                ("flags", wintypes.DWORD), ("wReserved", wintypes.WORD),
                ("XonLim", wintypes.WORD), ("XoffLim", wintypes.WORD),
                ("ByteSize", ctypes.c_ubyte), ("Parity", ctypes.c_ubyte),
                ("StopBits", ctypes.c_ubyte), ("XonChar", ctypes.c_char),
                ("XoffChar", ctypes.c_char), ("ErrorChar", ctypes.c_char),
                ("EofChar", ctypes.c_char), ("EvtChar", ctypes.c_char),
                ("wReserved1", wintypes.WORD),
            ]

        class COMMTIMEOUTS(ctypes.Structure):
            _fields_ = [
                ("ReadIntervalTimeout", wintypes.DWORD),
                ("ReadTotalTimeoutMultiplier", wintypes.DWORD),
                ("ReadTotalTimeoutConstant", wintypes.DWORD),
                ("WriteTotalTimeoutMultiplier", wintypes.DWORD),
                ("WriteTotalTimeoutConstant", wintypes.DWORD),
            ]

        self._ctypes, self._wintypes = ctypes, wintypes
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._kernel32.CreateFileW.restype = wintypes.HANDLE
        name = port if port.startswith("\\\\.\\") else "\\\\.\\" + port
        self._handle = self._kernel32.CreateFileW(
            name, 0xC0000000, 0, None, 3, 0, None
        )
        if self._handle == wintypes.HANDLE(-1).value:
            error = ctypes.get_last_error()
            raise OSError(error, ctypes.FormatError(error))

        try:
            dcb = DCB(); dcb.DCBlength = ctypes.sizeof(DCB)
            if not self._kernel32.GetCommState(self._handle, ctypes.byref(dcb)):
                self._raise_last_error()
            dcb.BaudRate = baudrate
            dcb.ByteSize = 8
            dcb.Parity = 0
            dcb.StopBits = 0
            # fBinary=1；关闭硬件/软件流控，DTR/RTS 保持启用。
            dcb.flags = 0x00000001 | 0x00000010 | 0x00001000
            if not self._kernel32.SetCommState(self._handle, ctypes.byref(dcb)):
                self._raise_last_error()
            timeouts = COMMTIMEOUTS(200, 0, 200, 0, 1000)
            if not self._kernel32.SetCommTimeouts(self._handle, ctypes.byref(timeouts)):
                self._raise_last_error()
            self._kernel32.SetupComm(self._handle, 4096, 4096)
            self._kernel32.PurgeComm(self._handle, 0x000F)
        except Exception:
            self.close()
            raise

    @property
    def is_open(self) -> bool:
        return self._handle is not None

    def _raise_last_error(self):
        error = self._ctypes.get_last_error()
        raise OSError(error, self._ctypes.FormatError(error))

    def write(self, data: bytes) -> int:
        buffer = self._ctypes.create_string_buffer(data)
        written = self._wintypes.DWORD()
        if not self._kernel32.WriteFile(
            self._handle, buffer, len(data), self._ctypes.byref(written), None
        ):
            self._raise_last_error()
        return written.value

    def flush(self) -> None:
        if not self._kernel32.FlushFileBuffers(self._handle):
            self._raise_last_error()

    def close(self) -> None:
        if self._handle is not None:
            self._kernel32.CloseHandle(self._handle)
            self._handle = None


@dataclass(frozen=True)
class LogEntry:
    time: str
    direction: str
    data: bytes
    note: str

    @property
    def text(self) -> str:
        return f"{self.time}  {self.direction:<3}  {frame_hex(self.data)}  {self.note}".rstrip()


class TimingController:
    def __init__(self, logger: Callable[[LogEntry], None] | None = None):
        self._serial = None
        self._simulation = False
        self._lock = threading.Lock()
        self._logger = logger

    @property
    def connected(self) -> bool:
        return self._simulation or bool(self._serial and self._serial.is_open)

    @property
    def simulation(self) -> bool:
        return self._simulation

    def _log(self, direction: str, data: bytes, note: str = "") -> None:
        if self._logger:
            self._logger(LogEntry(datetime.now().strftime("%H:%M:%S.%f")[:-3], direction, data, note))

    def open(self, port: str, baudrate: int = 115200, simulation: bool = False) -> None:
        self.close()
        self._simulation = simulation
        if simulation:
            self._log("SYS", b"", "仿真连接已建立")
            return
        try:
            if sys.platform == "win32":
                self._serial = _WindowsSerial(port, baudrate)
            else:
                import serial
                self._serial = serial.Serial(
                    port=port, baudrate=baudrate, bytesize=serial.EIGHTBITS,
                    parity=serial.PARITY_NONE, stopbits=serial.STOPBITS_ONE,
                    timeout=0.2, write_timeout=1.0,
                )
        except ImportError as exc:
            raise TimingControllerError("当前系统需要 pyserial，请执行：pip install pyserial") from exc
        except Exception as exc:
            raise TimingControllerError(f"无法打开串口 {port}：{exc}") from exc
        self._log("SYS", b"", f"已连接 {port} @ {baudrate} 8N1")

    def close(self) -> None:
        if self._serial:
            try:
                if self._serial.is_open:
                    self._serial.close()
            finally:
                self._serial = None
        self._simulation = False

    def send(self, data: bytes, note: str = "") -> None:
        if not self.connected:
            raise TimingControllerError("控制器尚未连接")
        with self._lock:
            if not self._simulation:
                try:
                    written = self._serial.write(data)
                    self._serial.flush()
                    if written != len(data):
                        raise TimingControllerError(f"串口仅写入 {written}/{len(data)} 字节")
                except TimingControllerError:
                    raise
                except Exception as exc:
                    raise TimingControllerError(f"串口发送失败：{exc}") from exc
            self._log("TX", data, note + ("（仿真）" if self._simulation else ""))

    def apply(self, parameters: TimingParameters) -> bytes:
        frame = build_parameter_frame(parameters)
        self.send(frame, "参数帧")
        return frame

    def run(self, parameters: TimingParameters) -> bytes:
        frame = build_parameter_frame(parameters)
        self.send(frame, "运行帧")
        return frame

    def stop(self) -> None:
        self.send(STOP_FRAME, "停止")


def available_ports() -> list[tuple[str, str]]:
    if sys.platform == "win32":
        try:
            import winreg
            ports = []
            key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DEVICEMAP\SERIALCOMM")
            index = 0
            while True:
                try:
                    name, device, _ = winreg.EnumValue(key, index)
                    ports.append((device, name))
                    index += 1
                except OSError:
                    break
            winreg.CloseKey(key)
            return sorted(ports, key=lambda item: item[0])
        except OSError:
            return []
    try:
        from serial.tools import list_ports
        return [(p.device, p.description or "") for p in list_ports.comports()]
    except ImportError:
        return []
