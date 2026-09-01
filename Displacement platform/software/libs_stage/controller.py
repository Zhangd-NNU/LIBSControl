from __future__ import annotations

import ctypes
import threading
import time
from pathlib import Path
from typing import Callable

from .paths import Point


class StageError(RuntimeError):
    pass


class FeinixsController:
    SAFE_MAX_VELOCITY = 10.0  # mm/s，参考配套软件的默认最大速度

    def __init__(self, dll_path: str | Path, simulate: bool = False):
        self.simulate = simulate
        self.dll_path = Path(dll_path)
        self.dll = None
        self.handle = ctypes.c_uint64(0)
        self.connected = False
        self.positions = {"1": 0.0, "2": 0.0, "3": 0.0}
        self.raw_positions = {"1": 0.0, "2": 0.0, "3": 0.0}
        self.origin_positions = {"1": 0.0, "2": 0.0, "3": 0.0}
        self.available_axes: set[str] = set()
        self.axis_info: dict[str, dict[str, object]] = {}
        self._stop = threading.Event()

    def _configure_dll(self) -> None:
        d = self.dll
        h, s, fp = ctypes.c_uint64, ctypes.c_char_p, ctypes.POINTER(ctypes.c_float)
        d.fti_open_imc.argtypes = [s, ctypes.c_int, ctypes.POINTER(h)]
        d.fti_open_imc.restype = ctypes.c_int
        d.fti_close.argtypes = [h]
        d.fti_close.restype = ctypes.c_int
        d.fti_set_vel.argtypes = [h, s, ctypes.c_float]
        d.fti_single_setenabled.argtypes = [h, s, ctypes.c_ubyte]
        d.fti_single_moveabs.argtypes = [h, s, ctypes.c_float]
        d.fti_single_move.argtypes = [h, s, ctypes.c_float]
        d.fti_single_getpos.argtypes = [h, s, fp]
        d.fti_single_getstatus.argtypes = [h, s, ctypes.POINTER(ctypes.c_uint32)]
        d.fti_single_isrunning.argtypes = [ctypes.c_uint32, ctypes.POINTER(ctypes.c_ubyte)]
        d.fti_single_getlimits.argtypes = [ctypes.c_uint32, ctypes.POINTER(ctypes.c_ubyte)]
        d.fti_single_stop.argtypes = [h, s]
        d.fti_single_home.argtypes = [h, s]
        d.fti_single_zero.argtypes = [h, s]
        d.fti_get_motor_model.argtypes = [h, s, ctypes.POINTER(ctypes.c_char)]
        d.fti_get_pitch.argtypes = [h, s, fp]
        d.fti_get_div.argtypes = [h, s, ctypes.POINTER(ctypes.c_int32)]
        d.fti_get_vel.argtypes = [h, s, fp]
        d.fti_get_sw_p1.argtypes = [h, s, fp]
        d.fti_get_sw_p2.argtypes = [h, s, fp]

    @staticmethod
    def _check(code: int, action: str) -> None:
        if code != 0:
            raise StageError(f"{action}失败，SDK 错误码 0x{code:04X}")

    def connect(self, endpoint: str, baud_or_port: int) -> None:
        if self.simulate:
            self.connected = True
            for axis in ("1", "2", "3"):
                self.raw_positions[axis] = self.origin_positions[axis] + self.positions[axis]
                self.origin_positions[axis] = self.raw_positions[axis]
                self.positions[axis] = 0.0
            self.available_axes = {"1", "2", "3"}
            self.axis_info = {
                axis: {"model": "SIM-IMC", "position_mm": 0.0, "pitch_mm": 1.0,
                       "division": 640, "velocity_mm_s": 10.0,
                       "soft_limits_raw_mm": (-50.0, 50.0)}
                for axis in self.available_axes
            }
            return
        if not self.dll_path.exists():
            raise StageError(f"找不到厂商 DLL：{self.dll_path}")
        self.dll = ctypes.WinDLL(str(self.dll_path))
        self._configure_dll()
        self._check(self.dll.fti_open_imc(endpoint.encode("ascii"), baud_or_port, ctypes.byref(self.handle)), "连接")
        self.connected = True
        # SDK 文档说明 open 只建立连接；扫描轴并读取位置用于验证真实通信。
        try:
            self.discover_axes()
            # 软件连接后的当前位置即为本次工作的坐标原点。
            self.zero()
        except Exception:
            self.close()
            raise

    @staticmethod
    def _decode_model(raw: bytes) -> str:
        for encoding in ("utf-8", "gbk", "ascii"):
            try:
                return raw.decode(encoding).strip()
            except UnicodeDecodeError:
                continue
        return raw.decode("latin1", errors="replace").strip()

    def discover_axes(self) -> dict[str, dict[str, object]]:
        if self.simulate:
            return dict(self.axis_info)
        found: dict[str, dict[str, object]] = {}
        for axis in ("1", "2", "3"):
            axis_b = axis.encode()
            position = ctypes.c_float()
            if self.dll.fti_single_getpos(self.handle, axis_b, ctypes.byref(position)) != 0:
                continue
            self.raw_positions[axis] = position.value
            self.positions[axis] = position.value - self.origin_positions[axis]
            model_buffer = ctypes.create_string_buffer(256)
            model_code = self.dll.fti_get_motor_model(self.handle, axis_b, model_buffer)
            info: dict[str, object] = {
                "model": self._decode_model(model_buffer.value) if model_code == 0 and model_buffer.value else "未知型号",
                "position_mm": self.positions[axis],
            }
            optional = (
                ("pitch_mm", self.dll.fti_get_pitch, ctypes.c_float),
                ("division", self.dll.fti_get_div, ctypes.c_int32),
                ("velocity_mm_s", self.dll.fti_get_vel, ctypes.c_float),
                ("soft_min_mm", self.dll.fti_get_sw_p1, ctypes.c_float),
                ("soft_max_mm", self.dll.fti_get_sw_p2, ctypes.c_float),
            )
            for key, function, value_type in optional:
                value = value_type()
                if function(self.handle, axis_b, ctypes.byref(value)) == 0:
                    info[key] = value.value
            if "soft_min_mm" in info and "soft_max_mm" in info:
                info["soft_limits_raw_mm"] = (info.pop("soft_min_mm"), info.pop("soft_max_mm"))
            found[axis] = info
        if not found:
            raise StageError("未识别到任何轴，无法验证位移平台通信")
        self.axis_info = found
        self.available_axes = set(found)
        return dict(found)

    def _require_axis(self, axis: str) -> None:
        if axis not in self.available_axes:
            name = {"1": "X", "2": "Y", "3": "Z"}.get(axis, axis)
            raise StageError(f"未识别到{name}轴（设备地址{axis}）")

    def close(self) -> None:
        if self.connected and not self.simulate and self.dll:
            self.dll.fti_close(self.handle)
        self.connected = False
        self.available_axes.clear()
        self.axis_info.clear()

    def get_position(self, axis: str) -> float:
        self._require_axis(axis)
        if self.simulate:
            return self.positions[axis]
        value = ctypes.c_float()
        self._check(self.dll.fti_single_getpos(self.handle, axis.encode(), ctypes.byref(value)), f"读取轴 {axis} 位置")
        self.raw_positions[axis] = value.value
        self.positions[axis] = value.value - self.origin_positions[axis]
        return self.positions[axis]

    def set_enabled(self, axis: str, enabled: bool) -> None:
        self._require_axis(axis)
        if not self.simulate:
            self._check(self.dll.fti_single_setenabled(self.handle, axis.encode(), int(enabled)), f"轴 {axis} 使能")

    def set_velocity(self, axis: str, velocity: float) -> None:
        self._require_axis(axis)
        if velocity <= 0:
            raise StageError("移动速度必须大于 0 mm/s")
        if velocity > self.SAFE_MAX_VELOCITY:
            raise StageError(
                f"设置速度 {velocity:g} mm/s 超过安全上限 {self.SAFE_MAX_VELOCITY:g} mm/s"
            )
        if not self.simulate:
            self._check(self.dll.fti_set_vel(self.handle, axis.encode(), ctypes.c_float(velocity)), f"设置轴 {axis} 速度")

    def _is_running(self, axis: str) -> bool:
        if self.simulate:
            return False
        status, running = ctypes.c_uint32(), ctypes.c_ubyte()
        self._check(self.dll.fti_single_getstatus(self.handle, axis.encode(), ctypes.byref(status)), "读取状态")
        self._check(self.dll.fti_single_isrunning(status, ctypes.byref(running)), "解析状态")
        return bool(running.value)

    def get_limit_state(self, axis: str) -> tuple[bool, bool]:
        """返回（负向限位，正向限位）的硬件触发状态。"""
        self._require_axis(axis)
        if self.simulate:
            return False, False
        status = ctypes.c_uint32()
        limits = (ctypes.c_ubyte * 2)()
        self._check(self.dll.fti_single_getstatus(self.handle, axis.encode(), ctypes.byref(status)), "读取限位状态")
        self._check(self.dll.fti_single_getlimits(status, limits), "解析限位状态")
        return bool(limits[0]), bool(limits[1])

    def _check_target_limit(self, axis: str, target: float) -> None:
        limits = self.axis_info.get(axis, {}).get("soft_limits_raw_mm")
        if not limits or limits[0] >= limits[1]:
            return
        name = {"1": "X", "2": "Y", "3": "Z"}[axis]
        raw_target = target + self.origin_positions[axis]
        negative_limit = limits[0] - self.origin_positions[axis]
        positive_limit = limits[1] - self.origin_positions[axis]
        if raw_target <= limits[0]:
            raise StageError(f"{name}轴目标位置 {target:.3f} mm 已达到负向极限 {negative_limit:.3f} mm")
        if raw_target >= limits[1]:
            raise StageError(f"{name}轴目标位置 {target:.3f} mm 已达到正向极限 {positive_limit:.3f} mm")

    def _check_hardware_limit(self, axis: str, direction: int) -> None:
        if self.simulate or direction == 0:
            return
        negative, positive = self.get_limit_state(axis)
        name = {"1": "X", "2": "Y", "3": "Z"}[axis]
        if direction < 0 and negative:
            self.dll.fti_single_stop(self.handle, axis.encode())
            raise StageError(f"{name}轴已达到负向极限，运动已停止")
        if direction > 0 and positive:
            self.dll.fti_single_stop(self.handle, axis.encode())
            raise StageError(f"{name}轴已达到正向极限，运动已停止")

    def move_xy(self, point: Point, velocity: float, timeout: float = 120.0) -> None:
        self._require_axis("1")
        self._require_axis("2")
        if velocity <= 0:
            raise StageError("扫描速度必须大于 0 mm/s")
        if velocity > self.SAFE_MAX_VELOCITY:
            raise StageError(
                f"扫描速度 {velocity:g} mm/s 超过安全上限 {self.SAFE_MAX_VELOCITY:g} mm/s"
            )
        if self._stop.is_set():
            raise StageError("扫描已停止")
        directions = {
            "1": 1 if point.x > self.positions["1"] else (-1 if point.x < self.positions["1"] else 0),
            "2": 1 if point.y > self.positions["2"] else (-1 if point.y < self.positions["2"] else 0),
        }
        self._check_target_limit("1", point.x)
        self._check_target_limit("2", point.y)
        if self.simulate:
            distance = max(abs(point.x - self.positions["1"]), abs(point.y - self.positions["2"]))
            time.sleep(min(distance / max(velocity, 0.001), 0.15))
            self.raw_positions.update({"1": point.x + self.origin_positions["1"],
                                       "2": point.y + self.origin_positions["2"]})
            self.positions.update({"1": point.x, "2": point.y})
            return
        for axis in ("1", "2"):
            self.set_velocity(axis, velocity)
            self.set_enabled(axis, True)
        # 连续下发两轴命令，使两轴近似同步启动。
        raw_x = point.x + self.origin_positions["1"]
        raw_y = point.y + self.origin_positions["2"]
        self._check(self.dll.fti_single_moveabs(self.handle, b"1", ctypes.c_float(raw_x)), "X 轴移动")
        self._check(self.dll.fti_single_moveabs(self.handle, b"2", ctypes.c_float(raw_y)), "Y 轴移动")
        deadline = time.monotonic() + timeout
        while True:
            self._check_hardware_limit("1", directions["1"])
            self._check_hardware_limit("2", directions["2"])
            if not (self._is_running("1") or self._is_running("2")):
                break
            if self._stop.wait(0.03):
                self.stop()
                raise StageError("扫描已停止")
            if time.monotonic() > deadline:
                self.stop()
                raise StageError("运动等待超时，已停止")
        self.positions.update({"1": self.get_position("1"), "2": self.get_position("2")})

    def move_axis_relative(self, axis: str, distance: float, velocity: float, timeout: float = 120.0) -> float:
        if axis not in ("1", "2", "3"):
            raise StageError(f"无效轴号：{axis}")
        self._require_axis(axis)
        if not self.connected:
            raise StageError("请先连接位移平台")
        if velocity <= 0:
            raise StageError("手动速度必须大于 0")
        if velocity > self.SAFE_MAX_VELOCITY:
            raise StageError(
                f"手动速度 {velocity:g} mm/s 超过安全上限 {self.SAFE_MAX_VELOCITY:g} mm/s"
            )
        self._stop.clear()
        target = self.positions[axis] + distance
        self._check_target_limit(axis, target)
        if self.simulate:
            time.sleep(min(abs(distance) / velocity, 0.15))
            self.raw_positions[axis] += distance
            self.positions[axis] = self.raw_positions[axis] - self.origin_positions[axis]
            return self.positions[axis]
        self.set_velocity(axis, velocity)
        self.set_enabled(axis, True)
        self._check(self.dll.fti_single_move(self.handle, axis.encode(), ctypes.c_float(distance)), f"轴 {axis} 手动移动")
        deadline = time.monotonic() + timeout
        direction = 1 if distance > 0 else (-1 if distance < 0 else 0)
        while self._is_running(axis):
            self._check_hardware_limit(axis, direction)
            if self._stop.wait(0.03):
                self.dll.fti_single_stop(self.handle, axis.encode())
                raise StageError("手动移动已停止")
            if time.monotonic() > deadline:
                self.dll.fti_single_stop(self.handle, axis.encode())
                raise StageError(f"轴 {axis} 手动移动超时")
        self._check_hardware_limit(axis, direction)
        return self.get_position(axis)

    def run_path(self, points: list[Point], velocity: float, dwell_s: float, callback: Callable[[int, Point], None]) -> None:
        if not self.connected:
            raise StageError("请先连接位移平台")
        self._stop.clear()
        for index, point in enumerate(points, 1):
            self.move_xy(point, velocity)
            callback(index, point)
            if dwell_s and self._stop.wait(dwell_s):
                raise StageError("扫描已停止")

    def stop(self) -> None:
        self._stop.set()
        if self.connected and not self.simulate and self.dll:
            for axis in self.available_axes:
                self.dll.fti_single_stop(self.handle, axis.encode())

    def _wait_axes_stopped(self, axes: tuple[str, ...], timeout: float = 180.0) -> None:
        deadline = time.monotonic() + timeout
        while any(self._is_running(axis) for axis in axes):
            if self._stop.wait(0.05):
                raise StageError("轴运动已停止")
            if time.monotonic() > deadline:
                self.stop()
                raise StageError("等待轴运动完成超时")

    def _resolve_axes(self, axes: tuple[str, ...] | None) -> tuple[str, ...]:
        selected = tuple(sorted(self.available_axes)) if axes is None else axes
        if not selected:
            raise StageError("没有可操作的已识别轴")
        for axis in selected:
            self._require_axis(axis)
        return selected

    def home(self, axes: tuple[str, ...] | None = None) -> dict[str, float]:
        axes = self._resolve_axes(axes)
        if self.simulate:
            for axis in axes:
                self.raw_positions[axis] = self.origin_positions[axis]
                self.positions[axis] = 0.0
            return dict(self.positions)
        self._stop.clear()
        directions: dict[str, int] = {}
        for axis in axes:
            current = self.get_position(axis)
            directions[axis] = -1 if current > 0 else (1 if current < 0 else 0)
            self._check(self.dll.fti_single_setenabled(self.handle, axis.encode(), 1), "回零前使能")
            self._check(
                self.dll.fti_single_moveabs(
                    self.handle, axis.encode(), ctypes.c_float(self.origin_positions[axis])
                ),
                f"轴 {axis} 移动到连接原点",
            )
        deadline = time.monotonic() + 180.0
        while True:
            for axis in axes:
                self._check_hardware_limit(axis, directions[axis])
            if not any(self._is_running(axis) for axis in axes):
                break
            if self._stop.wait(0.05):
                self.stop()
                raise StageError("回零运动已停止")
            if time.monotonic() > deadline:
                self.stop()
                raise StageError("回零运动超时")
        for axis in axes:
            self.positions[axis] = self.get_position(axis)
            if axis in self.axis_info:
                self.axis_info[axis]["position_mm"] = self.positions[axis]
        return dict(self.positions)

    def zero(self, axes: tuple[str, ...] | None = None) -> dict[str, float]:
        axes = self._resolve_axes(axes)
        if self.simulate:
            for axis in axes:
                self.raw_positions[axis] = self.origin_positions[axis] + self.positions[axis]
                self.origin_positions[axis] = self.raw_positions[axis]
                self.positions[axis] = 0.0
            return dict(self.positions)
        for axis in axes:
            self._check(self.dll.fti_single_zero(self.handle, axis.encode()), "当前位置置零")
        for axis in axes:
            raw = ctypes.c_float()
            self._check(self.dll.fti_single_getpos(self.handle, axis.encode(), ctypes.byref(raw)), f"读取轴 {axis} 置零位置")
            self.raw_positions[axis] = raw.value
            self.origin_positions[axis] = raw.value
            self.positions[axis] = 0.0
            if axis in self.axis_info:
                self.axis_info[axis]["position_mm"] = self.positions[axis]
        return dict(self.positions)
