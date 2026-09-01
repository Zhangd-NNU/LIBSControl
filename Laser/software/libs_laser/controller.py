from __future__ import annotations

import threading
import time
from dataclasses import dataclass, replace
from typing import Callable

from .protocol import (
    Command,
    Frame,
    FrameStreamParser,
    ProtocolError,
    byte_value_frame,
    describe_frame,
    link_frame,
    single_trigger_frame,
    switch_frame,
    voltage_frame,
)


class LaserError(RuntimeError):
    pass


@dataclass(slots=True)
class LaserState:
    serial_connected: bool = False
    online: bool = False
    voltage_v: int | None = None
    frequency_hz: int | None = None
    divider: int | None = None
    trigger_mode: str = "internal"
    prefire: bool = False
    working: bool = False
    q_enabled: bool = False
    single_mode: bool = False
    water_fault: bool = False
    door_fault: bool = False
    last_response_at: float | None = None

    @property
    def output_active(self) -> bool:
        return self.online and self.prefire and self.working and self.q_enabled

    @property
    def output_frequency_hz(self) -> float | None:
        if self.frequency_hz is None or self.divider is None:
            return None
        if self.divider == 0:
            return 0.0
        return self.frequency_hz / self.divider


@dataclass(frozen=True, slots=True)
class ControllerEvent:
    direction: str
    message: str
    raw: bytes = b""
    timestamp: float = 0.0


class LaserController:
    TRIGGER_MODES = {
        "internal": 0x00,
        "external_q": 0x01,
        "external_no_q": 0x02,
    }

    def __init__(
        self,
        transport,
        *,
        timeout: float = 0.9,
        retries: int = 1,
        event_callback: Callable[[ControllerEvent], None] | None = None,
        state_callback: Callable[[LaserState], None] | None = None,
    ) -> None:
        self.transport = transport
        self.timeout = timeout
        self.retries = retries
        self.event_callback = event_callback
        self.state_callback = state_callback
        self.state = LaserState()
        self._parser = FrameStreamParser()
        self._condition = threading.Condition()
        self._rx_sequence = 0
        self._received: list[tuple[int, Frame]] = []
        self._operation_lock = threading.RLock()

    def _emit_event(self, direction: str, message: str, raw: bytes = b"") -> None:
        if self.event_callback:
            self.event_callback(ControllerEvent(direction, message, raw, time.time()))

    def _emit_state(self) -> None:
        if self.state_callback:
            self.state_callback(replace(self.state))

    def connect(self) -> None:
        with self._operation_lock:
            if self.state.serial_connected:
                raise LaserError("串口已经连接")
            self._parser.reset()
            try:
                self.transport.open(self._on_bytes)
            except Exception as exc:
                raise LaserError(str(exc)) from exc
            self.state = LaserState(serial_connected=True)
            self._emit_event("SYS", "串口已打开（19200 / 8N1）")
            self._emit_state()

    def disconnect(self, safe: bool = True) -> None:
        with self._operation_lock:
            if not self.state.serial_connected:
                return
            if safe and self.state.online:
                self.emergency_stop()
                try:
                    self._transact(link_frame(False), "断开设备联机", timeout=min(self.timeout, 0.4), retries=0)
                except LaserError as exc:
                    self._emit_event("WARN", f"联机断开未确认：{exc}")
            try:
                self.transport.close()
            finally:
                self.state = LaserState()
                self._emit_event("SYS", "串口已关闭")
                self._emit_state()

    def _on_bytes(self, chunk: bytes) -> None:
        if not chunk:
            self._emit_event("WARN", "串口读取异常或连接已中断")
            return
        for frame in self._parser.feed(chunk):
            self._emit_event("RX", describe_frame(frame), frame.to_bytes())
            self._apply_frame(frame)
            with self._condition:
                self._rx_sequence += 1
                self._received.append((self._rx_sequence, frame))
                if len(self._received) > 80:
                    del self._received[:-40]
                self._condition.notify_all()
            if frame.command == Command.FAULT:
                threading.Thread(target=self._stop_after_fault, name="laser-fault-stop", daemon=True).start()

    def _stop_after_fault(self) -> None:
        try:
            self.emergency_stop()
        except LaserError as exc:
            self._emit_event("WARN", f"故障自动停机发送异常：{exc}")

    def _apply_frame(self, frame: Frame) -> None:
        command, value = frame.command, frame.data1
        if command == Command.LINK:
            if frame.data0 == 0 and value == 0x11:
                self.state.online = False
                self.state.prefire = self.state.working = self.state.q_enabled = False
            else:
                self.state.online = True
                self.state.voltage_v = (frame.data0 << 8) | frame.data1
                self.state.frequency_hz = frame.data3
        elif command == Command.VOLTAGE:
            self.state.voltage_v = (frame.data0 << 8) | frame.data1
        elif command == Command.DIVIDER:
            self.state.divider = value
        elif command == Command.FREQUENCY:
            self.state.frequency_hz = value
        elif command == Command.PREFIRE:
            self.state.prefire = value == 0x55
            if not self.state.prefire:
                self.state.working = self.state.q_enabled = False
        elif command == Command.WORK:
            if value != 0x11:
                self.state.working = value == 0x66
                if not self.state.working:
                    self.state.q_enabled = False
        elif command == Command.CLOCK:
            self.state.trigger_mode = {
                0x00: "internal",
                0x77: "external",
                0x01: "external_q",
                0x02: "external_no_q",
            }.get(value, self.state.trigger_mode)
        elif command == Command.SINGLE_MODE:
            self.state.single_mode = value == 0xAA
        elif command == Command.Q_SWITCH:
            self.state.q_enabled = value == 0xBB
        elif command == Command.FAULT:
            if value == 0x01:
                self.state.water_fault = True
            elif value == 0x02:
                self.state.door_fault = True
        self.state.last_response_at = time.time()
        self._emit_state()

    def _transact(
        self,
        frame: Frame,
        action: str,
        *,
        timeout: float | None = None,
        retries: int | None = None,
    ) -> Frame:
        if not self.state.serial_connected:
            raise LaserError("串口未连接")
        wait_time = self.timeout if timeout is None else timeout
        retry_count = self.retries if retries is None else retries
        for attempt in range(retry_count + 1):
            with self._condition:
                start_sequence = self._rx_sequence
                raw = frame.to_bytes()
                self._emit_event("TX", action, raw)
                try:
                    self.transport.write(raw)
                except Exception as exc:
                    raise LaserError(f"{action}发送失败：{exc}") from exc
                deadline = time.monotonic() + wait_time
                while True:
                    for sequence, received in self._received:
                        if sequence > start_sequence and received.command == frame.command:
                            return received
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    self._condition.wait(remaining)
            if attempt < retry_count:
                self._emit_event("WARN", f"{action}未收到响应，正在重试 {attempt + 1}/{retry_count}")
        raise LaserError(f"{action}超时：未收到命令 0x{frame.command:02X} 的上传帧")

    def _require_online(self, *, allow_fault: bool = False) -> None:
        if not self.state.serial_connected:
            raise LaserError("请先连接串口")
        if not self.state.online:
            raise LaserError("请先执行设备联机")
        if not allow_fault and self.state.water_fault:
            raise LaserError("设备上报水流故障，禁止启动")
        if not allow_fault and self.state.door_fault:
            raise LaserError("设备上报门开关联锁故障，禁止启动")

    def _require_idle(self) -> None:
        self._require_online()
        if self.state.working or self.state.q_enabled:
            raise LaserError("工作或 Q 输出期间禁止修改参数，请先安全停机")

    def set_online(self, enabled: bool) -> None:
        with self._operation_lock:
            if enabled:
                if not self.state.serial_connected:
                    raise LaserError("请先连接串口")
                self._transact(link_frame(True), "设备联机")
            else:
                if self.state.online:
                    self.emergency_stop()
                    self._transact(link_frame(False), "设备联机断开")

    def set_voltage(self, voltage: int) -> None:
        with self._operation_lock:
            self._require_idle()
            try:
                frame = voltage_frame(int(voltage))
            except (ValueError, ProtocolError) as exc:
                raise LaserError(str(exc)) from exc
            self._transact(frame, f"设置本振电压 {voltage} V")

    def set_frequency(self, frequency: int) -> None:
        with self._operation_lock:
            self._require_idle()
            if not 1 <= int(frequency) <= 255:
                raise LaserError("灯频率必须在 1-255 Hz 范围内，并不得超过设备额定频率")
            self._transact(byte_value_frame(Command.FREQUENCY, int(frequency)), f"设置灯频率 {frequency} Hz")

    def set_divider(self, divider: int) -> None:
        with self._operation_lock:
            self._require_idle()
            if not 0 <= int(divider) <= 255:
                raise LaserError("分频系数必须在 0-255 范围内；0 表示单次模式")
            if self.state.single_mode and int(divider) != 0:
                raise LaserError("请先退出单次模式，再设置连续工作的分频系数")
            self._transact(byte_value_frame(Command.DIVIDER, int(divider)), f"设置分频系数 {divider}")

    def apply_parameters(self, voltage: int, frequency: int, divider: int) -> None:
        with self._operation_lock:
            self.set_voltage(voltage)
            self.set_frequency(frequency)
            self.set_divider(divider)

    def set_trigger_mode(self, mode: str) -> None:
        with self._operation_lock:
            self._require_idle()
            if mode not in self.TRIGGER_MODES:
                raise LaserError(f"未知触发模式：{mode}")
            if self.state.single_mode:
                raise LaserError("请先退出单次模式，再切换触发方式")
            value = self.TRIGGER_MODES[mode]
            if mode.startswith("external"):
                self._transact(byte_value_frame(Command.CLOCK, 0x77), "开启外时序")
            labels = {"internal": "内时序", "external_q": "外时序（外部 Q）", "external_no_q": "外时序（内部 Q）"}
            self._transact(byte_value_frame(Command.CLOCK, value), f"切换到{labels[mode]}")

    def set_prefire(self, enabled: bool) -> None:
        with self._operation_lock:
            self._require_online(allow_fault=not enabled)
            if not enabled:
                if self.state.q_enabled:
                    self.set_q(False)
                if self.state.working:
                    self.set_work(False)
            self._transact(switch_frame(Command.PREFIRE, enabled), "开启预燃" if enabled else "关闭预燃")

    def set_work(self, enabled: bool) -> None:
        with self._operation_lock:
            self._require_online(allow_fault=not enabled)
            if enabled and not self.state.prefire:
                raise LaserError("必须先开启预燃，才能开启工作")
            if not enabled and self.state.q_enabled:
                self.set_q(False)
            self._transact(switch_frame(Command.WORK, enabled), "开启工作" if enabled else "停止工作")

    def set_q(self, enabled: bool) -> None:
        with self._operation_lock:
            self._require_online(allow_fault=not enabled)
            if enabled and not (self.state.prefire and self.state.working):
                raise LaserError("必须先开启预燃和工作，才能开启 Q 输出")
            if enabled and self.state.trigger_mode == "external_q":
                raise LaserError("当前为外时序（外部 Q），Q 输出由 Q IN 信号控制")
            if enabled and self.state.single_mode:
                raise LaserError("单次模式下请使用“单次激光触发”，不能连续开启 Q")
            self._transact(switch_frame(Command.Q_SWITCH, enabled), "开启 Q 输出" if enabled else "关闭 Q 输出")

    def set_single_mode(self, enabled: bool) -> None:
        with self._operation_lock:
            self._require_idle()
            if enabled and self.state.trigger_mode != "internal":
                raise LaserError("单次模式仅允许在内时序下开启")
            if enabled and self.state.divider != 0:
                self.set_divider(0)
            self._transact(switch_frame(Command.SINGLE_MODE, enabled), "开启单次模式" if enabled else "关闭单次模式")

    def single_trigger(self) -> None:
        with self._operation_lock:
            self._require_online()
            if not self.state.single_mode or self.state.divider != 0:
                raise LaserError("请先开启单次模式（分频系数将设为 0）")
            if not (self.state.prefire and self.state.working):
                raise LaserError("单次触发前必须开启预燃和工作")
            self._transact(single_trigger_frame(), "单次激光触发")

    def emergency_stop(self) -> None:
        """不等待应答，立即按 Q→工作→预燃顺序下发关闭帧。"""
        with self._operation_lock:
            if not self.state.serial_connected:
                return
            commands = (
                (switch_frame(Command.Q_SWITCH, False), "紧急关闭 Q 输出"),
                (switch_frame(Command.WORK, False), "紧急停止工作"),
                (switch_frame(Command.PREFIRE, False), "紧急关闭预燃"),
            )
            errors: list[str] = []
            for frame, action in commands:
                raw = frame.to_bytes()
                self._emit_event("TX", action, raw)
                try:
                    self.transport.write(raw)
                except Exception as exc:
                    errors.append(str(exc))
            self.state.q_enabled = self.state.working = self.state.prefire = False
            self._emit_state()
            self._emit_event("SYS", "已下发安全停机序列：关 Q → 停工作 → 关预燃")
            if errors:
                raise LaserError("；".join(errors))
