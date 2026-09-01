from __future__ import annotations

import ctypes
import os
import threading
from ctypes import wintypes
from typing import Callable

from .protocol import Command, Frame, FrameStreamParser


class SerialPortError(OSError):
    pass


def list_serial_ports() -> list[str]:
    if os.name != "nt":
        return []
    try:
        import winreg

        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DEVICEMAP\SERIALCOMM")
        ports: list[str] = []
        index = 0
        while True:
            try:
                _, value, _ = winreg.EnumValue(key, index)
            except OSError:
                break
            ports.append(str(value))
            index += 1
        winreg.CloseKey(key)
        return sorted(set(ports), key=lambda item: (len(item), item))
    except OSError:
        return []


if os.name == "nt":
    class DCB(ctypes.Structure):
        _fields_ = [
            ("DCBlength", wintypes.DWORD),
            ("BaudRate", wintypes.DWORD),
            ("flags", wintypes.DWORD),
            ("wReserved", wintypes.WORD),
            ("XonLim", wintypes.WORD),
            ("XoffLim", wintypes.WORD),
            ("ByteSize", wintypes.BYTE),
            ("Parity", wintypes.BYTE),
            ("StopBits", wintypes.BYTE),
            ("XonChar", ctypes.c_char),
            ("XoffChar", ctypes.c_char),
            ("ErrorChar", ctypes.c_char),
            ("EofChar", ctypes.c_char),
            ("EvtChar", ctypes.c_char),
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


class WindowsSerialTransport:
    """仅用 Windows API 实现的 19200/8/N/1 串口，无第三方依赖。"""

    INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
    GENERIC_READ = 0x80000000
    GENERIC_WRITE = 0x40000000
    OPEN_EXISTING = 3
    PURGE_RXCLEAR = 0x0008
    PURGE_TXCLEAR = 0x0004

    def __init__(self, port: str, baudrate: int = 19200) -> None:
        self.port = port.strip().upper()
        self.baudrate = int(baudrate)
        self._handle = None
        self._callback: Callable[[bytes], None] | None = None
        self._stop = threading.Event()
        self._reader: threading.Thread | None = None
        self._write_lock = threading.Lock()

    @property
    def is_open(self) -> bool:
        return self._handle not in (None, self.INVALID_HANDLE_VALUE)

    @staticmethod
    def _raise_last_error(action: str) -> None:
        code = ctypes.get_last_error()
        raise SerialPortError(code, f"{action}失败：{ctypes.FormatError(code).strip()}")

    def open(self, callback: Callable[[bytes], None]) -> None:
        if os.name != "nt":
            raise SerialPortError("实机串口仅支持 Windows；可使用仿真模式")
        if self.is_open:
            raise SerialPortError("串口已经打开")
        if not self.port:
            raise SerialPortError("请选择串口")

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateFileW.restype = wintypes.HANDLE
        device = rf"\\.\{self.port}"
        handle = kernel32.CreateFileW(
            device,
            self.GENERIC_READ | self.GENERIC_WRITE,
            0,
            None,
            self.OPEN_EXISTING,
            0,
            None,
        )
        if handle == self.INVALID_HANDLE_VALUE:
            self._raise_last_error(f"打开 {self.port}")
        self._handle = handle
        try:
            dcb = DCB()
            dcb.DCBlength = ctypes.sizeof(DCB)
            if not kernel32.BuildCommDCBW(f"baud={self.baudrate} parity=n data=8 stop=1", ctypes.byref(dcb)):
                self._raise_last_error("生成串口参数")
            if not kernel32.SetCommState(handle, ctypes.byref(dcb)):
                self._raise_last_error("设置串口参数")
            timeouts = COMMTIMEOUTS(30, 0, 80, 0, 500)
            if not kernel32.SetCommTimeouts(handle, ctypes.byref(timeouts)):
                self._raise_last_error("设置串口超时")
            kernel32.SetupComm(handle, 4096, 4096)
            kernel32.PurgeComm(handle, self.PURGE_RXCLEAR | self.PURGE_TXCLEAR)
        except Exception:
            kernel32.CloseHandle(handle)
            self._handle = None
            raise

        self._callback = callback
        self._stop.clear()
        self._reader = threading.Thread(target=self._read_loop, name=f"laser-{self.port}-reader", daemon=True)
        self._reader.start()

    def _read_loop(self) -> None:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        buffer = ctypes.create_string_buffer(256)
        read = wintypes.DWORD()
        while not self._stop.is_set() and self.is_open:
            ok = kernel32.ReadFile(self._handle, buffer, len(buffer), ctypes.byref(read), None)
            if not ok:
                if not self._stop.is_set() and self._callback:
                    self._callback(b"")
                return
            if read.value and self._callback:
                self._callback(buffer.raw[: read.value])

    def write(self, data: bytes) -> None:
        if not self.is_open:
            raise SerialPortError("串口未打开")
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        written = wintypes.DWORD()
        payload = bytes(data)
        with self._write_lock:
            ok = kernel32.WriteFile(self._handle, payload, len(payload), ctypes.byref(written), None)
        if not ok:
            self._raise_last_error("串口写入")
        if written.value != len(payload):
            raise SerialPortError(f"串口写入不完整：{written.value}/{len(payload)} 字节")

    def close(self) -> None:
        self._stop.set()
        if self.is_open:
            ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle(self._handle)
        self._handle = None
        if self._reader and self._reader is not threading.current_thread():
            self._reader.join(timeout=0.3)
        self._reader = None
        self._callback = None


class SimulatedTransport:
    """协议级仿真器；生成与资料所列上传帧相同的响应。"""

    def __init__(self, response_delay: float = 0.005) -> None:
        self.response_delay = response_delay
        self._callback: Callable[[bytes], None] | None = None
        self._open = False
        self._parser = FrameStreamParser()
        self.voltage = 680
        self.frequency = 20
        self.divider = 1
        self.online = False
        self.prefire = False
        self.working = False
        self.q_enabled = False
        self.clock_value = 0
        self.single_mode = False

    @property
    def is_open(self) -> bool:
        return self._open

    def open(self, callback: Callable[[bytes], None]) -> None:
        if self._open:
            raise SerialPortError("仿真串口已经打开")
        self._callback = callback
        self._open = True

    def close(self) -> None:
        self._open = False
        self._callback = None

    def write(self, data: bytes) -> None:
        if not self._open:
            raise SerialPortError("仿真串口未打开")
        frames = self._parser.feed(data)
        if not frames:
            raise SerialPortError("仿真器收到无效协议帧")
        for frame in frames:
            response = self._handle(frame)
            timer = threading.Timer(self.response_delay, self._deliver, args=(response.to_bytes(),))
            timer.daemon = True
            timer.start()

    def _deliver(self, payload: bytes) -> None:
        if self._open and self._callback:
            self._callback(payload)

    def _handle(self, frame: Frame) -> Frame:
        value = frame.data1
        command = frame.command
        if command == Command.LINK:
            self.online = value != 0x11
            if self.online:
                return Frame(Command.LINK, self.voltage >> 8, self.voltage & 0xFF, 0, self.frequency)
            self.prefire = self.working = self.q_enabled = False
        elif command == Command.VOLTAGE:
            self.voltage = (frame.data0 << 8) | frame.data1
        elif command == Command.DIVIDER:
            self.divider = value
        elif command == Command.FREQUENCY:
            self.frequency = value
        elif command == Command.PREFIRE:
            self.prefire = value == 0x55
        elif command == Command.WORK:
            if value != 0x11:
                self.working = value == 0x66
        elif command == Command.CLOCK:
            self.clock_value = value
        elif command == Command.SINGLE_MODE:
            self.single_mode = value == 0xAA
        elif command == Command.Q_SWITCH:
            self.q_enabled = value == 0xBB
        return frame

    def inject_fault(self, code: int) -> None:
        if code not in (1, 2):
            raise ValueError("仿真故障代码仅支持 1（水流）或 2（门联锁）")
        self._deliver(Frame(Command.FAULT, 0, code).to_bytes())
