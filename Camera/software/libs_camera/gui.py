from __future__ import annotations

import queue
import sys
import threading
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageTk

from .config import load_config, save_config
from .controller import CameraError, CameraSystem
from .models import (
    MONITOR,
    ROLE_NAMES,
    ROLES,
    TRIGGER_A,
    TRIGGER_B,
    AppConfig,
    CameraDescriptor,
    CameraSettings,
    SaveSettings,
)


EDGE_NAMES = ("上升沿", "下降沿", "高电平", "低电平")
UNASSIGNED = "未分配"


class CameraApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("LIBS Camera Control · 三相机采集系统")
        self.geometry("1460x895")
        self.minsize(1120, 820)
        self.configure(bg="#000000")
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        # PyInstaller 调试包将可写配置和采集目录放在 EXE 同级，
        # 避免写入 _internal 依赖目录。
        self.root_dir = (
            Path(sys.executable).resolve().parent
            if getattr(sys, "frozen", False)
            else Path(__file__).resolve().parents[1]
        )
        self.config_path = self.root_dir / "config.json"
        self.app_config = load_config(self.config_path)
        self.events: queue.Queue[dict] = queue.Queue()
        self.system = CameraSystem(self.events.put)
        self.devices: list[CameraDescriptor] = []
        self.display_to_uid: dict[str, str] = {}
        self.preview_refs: dict[str, ImageTk.PhotoImage] = {}
        self.busy = False
        self.connected = False
        self.recording = False
        self._build_vars()
        self._configure_styles()
        self._build_ui()
        self.after_idle(self._fit_to_screen)
        self.after(80, self._poll_events)
        self.after(180, self.refresh_devices)

    def _fit_to_screen(self) -> None:
        self.update_idletasks()
        screen_w, screen_h = self.winfo_screenwidth(), self.winfo_screenheight()
        width = max(1120, min(1460, screen_w - 30))
        height = max(820, min(895, screen_h - 50))
        x = max(0, (screen_w - width) // 2)
        y = max(0, (screen_h - height) // 2 - 10)
        self.geometry(f"{width}x{height}+{x}+{y}")

    def _build_vars(self) -> None:
        configured_root = Path(self.app_config.output_root)
        if not configured_root.is_absolute():
            configured_root = self.root_dir / configured_root
        self.simulation = tk.BooleanVar(value=self.app_config.simulation)
        self.output_root = tk.StringVar(value=str(configured_root.resolve()))
        self.folder_name = tk.StringVar(value="")
        self.file_name = tk.StringVar(value="")
        self.image_format = tk.StringVar(value=self.app_config.image_format)
        self.jpeg_quality = tk.StringVar(value=str(self.app_config.jpeg_quality))
        self.save_enabled = tk.BooleanVar(value=self.app_config.save_enabled)
        self.role_selection = {role: tk.StringVar() for role in ROLES}
        self.settings_vars: dict[str, dict[str, tk.Variable]] = {}
        for role in ROLES:
            settings = self.app_config.settings_for(role)
            self.settings_vars[role] = {
                "auto_exposure": tk.BooleanVar(value=settings.auto_exposure),
                "exposure_ms": tk.StringVar(value=f"{settings.exposure_ms:g}"),
                "analog_gain": tk.StringVar(value=str(settings.analog_gain)),
                "trigger_edge": tk.StringVar(value=EDGE_NAMES[settings.trigger_edge]),
                "trigger_delay_us": tk.StringVar(value=str(settings.trigger_delay_us)),
                "trigger_jitter_us": tk.StringVar(value=str(settings.trigger_jitter_us)),
            }
        self.status = tk.StringVar(value="正在准备相机系统……")
        self.device_summary = tk.StringVar(value="设备：尚未扫描")
        self.session_status = tk.StringVar(value="采集会话：未启动")
        self.stats_vars = {
            role: tk.StringVar(value="接收 0  ·  保存 0  ·  丢帧 0") for role in ROLES
        }

    def _configure_styles(self) -> None:
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TFrame", background="#000000")
        style.configure("Panel.TFrame", background="#080808")
        style.configure("TLabel", background="#080808", foreground="#d8e1e6", font=("Microsoft YaHei UI", 9))
        style.configure("Panel.TLabel", background="#080808", foreground="#d8e1e6")
        style.configure("Title.TLabel", background="#000000", foreground="#40dcff", font=("Microsoft YaHei UI", 18, "bold"))
        style.configure("SubTitle.TLabel", background="#000000", foreground="#71818a", font=("Microsoft YaHei UI", 9))
        style.configure("ViewTitle.TLabel", background="#080808", foreground="#40dcff", font=("Segoe UI", 10, "bold"))
        style.configure("Hint.TLabel", background="#080808", foreground="#77878f", font=("Microsoft YaHei UI", 8))
        style.configure("Good.TLabel", background="#080808", foreground="#00f0b5", font=("Consolas", 9, "bold"))
        style.configure("Status.TLabel", background="#050505", foreground="#8de8ff", padding=8)
        style.configure("TLabelframe", background="#080808", bordercolor="#252525", relief="solid")
        style.configure("TLabelframe.Label", background="#080808", foreground="#40dcff", font=("Microsoft YaHei UI", 10, "bold"))
        style.configure("TEntry", fieldbackground="#111111", foreground="#f0f6f8", bordercolor="#303030", insertcolor="#40dcff", padding=5)
        style.configure("TCombobox", fieldbackground="#111111", foreground="#f0f6f8", arrowcolor="#40dcff", padding=4)
        style.map(
            "TCombobox",
            fieldbackground=[("readonly", "#111111"), ("focus", "#161616")],
            foreground=[("readonly", "#f0f6f8")],
            selectbackground=[("readonly", "#111111")],
            selectforeground=[("readonly", "#f0f6f8")],
        )
        self.option_add("*TCombobox*Listbox.background", "#111111")
        self.option_add("*TCombobox*Listbox.foreground", "#f0f6f8")
        self.option_add("*TCombobox*Listbox.selectBackground", "#007f9f")
        self.option_add("*TCombobox*Listbox.selectForeground", "#ffffff")
        style.configure("TCheckbutton", background="#080808", foreground="#c3cdd2")
        style.map("TCheckbutton", background=[("active", "#080808")])
        style.configure("TButton", background="#202020", foreground="#e8eef1", borderwidth=0, padding=(8, 5), font=("Microsoft YaHei UI", 9))
        style.map("TButton", background=[("active", "#353535"), ("pressed", "#141414")])
        style.configure("Accent.TButton", background="#00a8d6", foreground="#03121b", font=("Microsoft YaHei UI", 9, "bold"))
        style.map("Accent.TButton", background=[("active", "#38d5ff"), ("pressed", "#0086aa")])
        style.configure("Capture.TButton", background="#00aa7d", foreground="#001a12", font=("Microsoft YaHei UI", 9, "bold"))
        style.map("Capture.TButton", background=[("active", "#00f0b5"), ("pressed", "#008563")])
        style.configure("Danger.TButton", background="#b93b55", foreground="white")
        style.map("Danger.TButton", background=[("active", "#e34f6b"), ("pressed", "#8f2940")])
        style.configure("Tech.TSeparator", background="#252525")

    def _build_ui(self) -> None:
        self.columnconfigure(0, minsize=330)
        self.columnconfigure(1, weight=1, minsize=520)
        self.columnconfigure(2, minsize=330)
        self.rowconfigure(1, weight=1)

        header = ttk.Frame(self, padding=(18, 13, 18, 10))
        header.grid(row=0, column=0, columnspan=3, sticky="ew")
        ttk.Label(header, text="LIBS CAMERA CONTROL", style="Title.TLabel").pack(side="left")
        ttk.Label(header, text="双外触发采集 · 单路实时监控", style="SubTitle.TLabel").pack(side="left", padx=(16, 0), pady=(8, 0))
        ttk.Label(header, textvariable=self.device_summary, style="SubTitle.TLabel", anchor="e").pack(side="right", pady=(8, 0))

        self.left_panel = ttk.Frame(self, style="Panel.TFrame", padding=12)
        self.left_panel.grid(row=1, column=0, sticky="nsew", padx=(12, 6))
        self.center_panel = ttk.Frame(self, style="Panel.TFrame", padding=12)
        self.center_panel.grid(row=1, column=1, sticky="nsew", padx=6)
        self.right_panel = ttk.Frame(self, style="Panel.TFrame", padding=12)
        self.right_panel.grid(row=1, column=2, sticky="nsew", padx=(6, 12))

        self._build_left(self.left_panel)
        self._build_center(self.center_panel)
        self._build_right(self.right_panel)

        statusbar = ttk.Frame(self, style="Panel.TFrame")
        statusbar.grid(row=2, column=0, columnspan=3, sticky="ew", padx=12, pady=(8, 10))
        ttk.Label(statusbar, text="● SYSTEM", style="Status.TLabel").pack(side="left")
        ttk.Label(statusbar, textvariable=self.status, style="Status.TLabel").pack(side="left", fill="x", expand=True)

    def _build_left(self, parent: ttk.Frame) -> None:
        devices = ttk.LabelFrame(parent, text=" 设备与角色绑定 ", padding=8)
        devices.pack(fill="x", pady=(0, 8))
        devices.columnconfigure(0, weight=1)
        self.simulation_check = ttk.Checkbutton(devices, text="仿真模式（无需连接硬件）", variable=self.simulation)
        self.simulation_check.grid(row=0, column=0, sticky="w")
        self.refresh_button = ttk.Button(devices, text="刷新相机列表", command=self.refresh_devices)
        self.refresh_button.grid(row=0, column=1, sticky="e")
        self.role_combos: dict[str, ttk.Combobox] = {}
        for index, role in enumerate(ROLES):
            row = 1 + index * 2
            ttk.Label(devices, text=ROLE_NAMES[role]).grid(row=row, column=0, sticky="w", pady=(7, 2))
            combo = ttk.Combobox(devices, textvariable=self.role_selection[role], state="readonly", width=31)
            combo.grid(row=row + 1, column=0, columnspan=2, sticky="ew")
            self.role_combos[role] = combo
        buttons = ttk.Frame(devices, style="Panel.TFrame")
        buttons.grid(row=7, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        buttons.columnconfigure((0, 1), weight=1)
        self.connect_button = ttk.Button(buttons, text="连接所选相机", command=self.connect_devices, style="Accent.TButton")
        self.connect_button.grid(row=0, column=0, sticky="ew", padx=(0, 3))
        self.disconnect_button = ttk.Button(buttons, text="断开连接", command=self.disconnect_devices, state="disabled")
        self.disconnect_button.grid(row=0, column=1, sticky="ew", padx=(3, 0))

        output = ttk.LabelFrame(parent, text=" 采集文件 ", padding=8)
        output.pack(fill="x", pady=(0, 8))
        output.columnconfigure(0, weight=1)
        self.save_check = ttk.Checkbutton(
            output,
            text="保存外触发图片",
            variable=self.save_enabled,
            command=self._update_save_controls,
        )
        self.save_check.grid(row=0, column=0, columnspan=2, sticky="w")
        ttk.Label(output, text="关闭后仍采集并显示末帧，但不写入磁盘", style="Hint.TLabel").grid(row=1, column=0, columnspan=2, sticky="w", pady=(2, 6))
        ttk.Label(output, text="保存根目录").grid(row=2, column=0, sticky="w")
        self.output_path_entry = ttk.Entry(output, textvariable=self.output_root)
        self.output_path_entry.grid(row=3, column=0, sticky="ew", pady=(3, 0))
        self.output_browse_button = ttk.Button(output, text="浏览…", command=self.choose_output)
        self.output_browse_button.grid(row=3, column=1, padx=(5, 0), pady=(3, 0))
        self.file_names_frame = ttk.Frame(output, style="Panel.TFrame")
        self.file_names_frame.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(7, 0))
        self.file_names_frame.columnconfigure((1, 3), weight=1, uniform="file_name_value")
        ttk.Label(self.file_names_frame, text="文件夹名").grid(row=0, column=0, sticky="w")
        self.folder_name_entry = ttk.Entry(self.file_names_frame, textvariable=self.folder_name, width=6)
        self.folder_name_entry.grid(row=0, column=1, sticky="ew", padx=(6, 0))
        ttk.Label(self.file_names_frame, text="文件名").grid(row=0, column=2, sticky="w", padx=(12, 0))
        self.file_name_entry = ttk.Entry(self.file_names_frame, textvariable=self.file_name, width=6)
        self.file_name_entry.grid(row=0, column=3, sticky="ew", padx=(6, 0))
        self.file_options_frame = ttk.Frame(output, style="Panel.TFrame")
        self.file_options_frame.grid(row=5, column=0, columnspan=2, sticky="ew", pady=(7, 0))
        self.file_options_frame.columnconfigure((1, 3), weight=1, uniform="file_option_value")
        ttk.Label(self.file_options_frame, text="格式").grid(row=0, column=0, sticky="w")
        self.format_combo = ttk.Combobox(
            self.file_options_frame,
            textvariable=self.image_format,
            values=("PNG", "JPG", "BMP"),
            state="readonly",
            width=8,
        )
        self.format_combo.grid(row=0, column=1, sticky="ew", padx=(6, 0))
        ttk.Label(self.file_options_frame, text="JPG 质量").grid(row=0, column=2, sticky="w", padx=(12, 0))
        self.quality_entry = ttk.Entry(self.file_options_frame, textvariable=self.jpeg_quality, width=8)
        self.quality_entry.grid(row=0, column=3, sticky="ew", padx=(6, 0))
        ttk.Label(output, text="保存为：文件夹名 / Camera A(B) / 文件名-1", style="Hint.TLabel", wraplength=285).grid(row=6, column=0, columnspan=2, sticky="w", pady=(7, 0))
        self._update_save_controls()

        capture = ttk.LabelFrame(parent, text=" 外触发采集 ", padding=8)
        capture.pack(fill="x")
        capture.columnconfigure((0, 1), weight=1)
        self.start_button = ttk.Button(capture, text="开始等待外触发", command=self.start_recording, style="Capture.TButton", state="disabled")
        self.start_button.grid(row=0, column=0, columnspan=2, sticky="ew")
        self.stop_button = ttk.Button(capture, text="停止采集并写盘", command=self.stop_recording, style="Danger.TButton", state="disabled")
        self.stop_button.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(5, 0))
        self.trigger_all_button = ttk.Button(capture, text="测试触发 A + B", command=lambda: self.test_trigger(None), state="disabled")
        self.trigger_all_button.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(5, 0))
        self.trigger_a_button = ttk.Button(capture, text="测试 A", command=lambda: self.test_trigger(TRIGGER_A), state="disabled")
        self.trigger_a_button.grid(row=3, column=0, sticky="ew", padx=(0, 3), pady=(5, 0))
        self.trigger_b_button = ttk.Button(capture, text="测试 B", command=lambda: self.test_trigger(TRIGGER_B), state="disabled")
        self.trigger_b_button.grid(row=3, column=1, sticky="ew", padx=(3, 0), pady=(5, 0))
        ttk.Label(capture, textvariable=self.session_status, style="Hint.TLabel", wraplength=285).grid(row=4, column=0, columnspan=2, sticky="w", pady=(7, 0))

    def _build_center(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(1, weight=3)
        parent.rowconfigure(3, weight=2)
        ttk.Label(parent, text="LIVE MONITOR · 实时监控", style="ViewTitle.TLabel").grid(row=0, column=0, sticky="w", padx=5, pady=(0, 5))
        self.preview_labels: dict[str, tk.Label] = {}
        monitor = tk.Label(parent, text="等待监控相机画面", bg="#000000", fg="#52636c", font=("Microsoft YaHei UI", 13), bd=1, relief="solid")
        monitor.grid(row=1, column=0, sticky="nsew")
        self.preview_labels[MONITOR] = monitor
        ttk.Label(parent, textvariable=self.stats_vars[MONITOR], style="Good.TLabel").grid(row=2, column=0, sticky="w", padx=5, pady=(5, 7))

        triggered = ttk.Frame(parent, style="Panel.TFrame")
        triggered.grid(row=3, column=0, sticky="nsew")
        triggered.columnconfigure((0, 1), weight=1, uniform="trigger_preview")
        triggered.rowconfigure(1, weight=1)
        for column, role in enumerate((TRIGGER_A, TRIGGER_B)):
            ttk.Label(triggered, text=f"{ROLE_NAMES[role].upper()} · LAST FRAME", style="ViewTitle.TLabel").grid(row=0, column=column, sticky="w", padx=(2, 6), pady=(0, 5))
            label = tk.Label(triggered, text="等待触发帧", bg="#000000", fg="#52636c", font=("Microsoft YaHei UI", 11), bd=1, relief="solid")
            label.grid(row=1, column=column, sticky="nsew", padx=(0, 4) if column == 0 else (4, 0))
            self.preview_labels[role] = label
            ttk.Label(triggered, textvariable=self.stats_vars[role], style="Good.TLabel").grid(row=2, column=column, sticky="w", padx=3, pady=(5, 0))

    def _build_right(self, parent: ttk.Frame) -> None:
        self._camera_settings_panel(parent, TRIGGER_A, external=True)
        self._camera_settings_panel(parent, TRIGGER_B, external=True)
        self._camera_settings_panel(parent, MONITOR, external=False)
        self.apply_button = ttk.Button(parent, text="应用全部相机参数", command=self.apply_settings, style="Accent.TButton", state="disabled")
        self.apply_button.pack(fill="x", pady=(0, 8))

        log_frame = ttk.LabelFrame(parent, text=" 运行日志 ", padding=6)
        log_frame.pack(fill="both", expand=True)
        self.log_text = tk.Text(
            log_frame,
            height=8,
            bg="#050505",
            fg="#93a8b2",
            insertbackground="#40dcff",
            selectbackground="#164451",
            relief="flat",
            font=("Consolas", 8),
            wrap="word",
            state="disabled",
        )
        self.log_text.pack(fill="both", expand=True)
        self.log_text.tag_configure("error", foreground="#ff6685")
        self.log_text.tag_configure("info", foreground="#93a8b2")
        self.log_text.tag_configure("success", foreground="#00f0b5")

    def _camera_settings_panel(self, parent: ttk.Frame, role: str, external: bool) -> None:
        panel = ttk.LabelFrame(parent, text=f" {ROLE_NAMES[role]}参数 ", padding=7)
        panel.pack(fill="x", pady=(0, 7))
        panel.columnconfigure(1, weight=1)
        values = self.settings_vars[role]
        ttk.Checkbutton(panel, text="自动曝光", variable=values["auto_exposure"]).grid(row=0, column=0, columnspan=2, sticky="w")
        self._setting_entry(panel, "曝光时间 (ms)", values["exposure_ms"], 1)
        self._setting_entry(panel, "模拟增益 (1-256)", values["analog_gain"], 2)
        if external:
            ttk.Label(panel, text="触发信号").grid(row=3, column=0, sticky="w", pady=2)
            ttk.Combobox(panel, textvariable=values["trigger_edge"], values=EDGE_NAMES, state="readonly", width=11).grid(row=3, column=1, sticky="ew", pady=2)
            self._setting_entry(panel, "触发延时 (µs)", values["trigger_delay_us"], 4)
            self._setting_entry(panel, "去抖时间 (µs)", values["trigger_jitter_us"], 5)

    @staticmethod
    def _setting_entry(parent: ttk.Frame, text: str, variable: tk.Variable, row: int) -> None:
        ttk.Label(parent, text=text).grid(row=row, column=0, sticky="w", padx=(0, 6), pady=2)
        ttk.Entry(parent, textvariable=variable, width=12).grid(row=row, column=1, sticky="ew", pady=2)

    def _log(self, message: str, level: str = "info") -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_text.configure(state="normal")
        self.log_text.insert("end", f"[{timestamp}] {message}\n", level)
        lines = int(self.log_text.index("end-1c").split(".")[0])
        if lines > 300:
            self.log_text.delete("1.0", f"{lines - 250}.0")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def refresh_devices(self) -> None:
        if self.busy or self.connected:
            return
        self._set_busy(True, "正在扫描相机……")
        simulation = self.simulation.get()

        def worker() -> None:
            try:
                self.system.enumerate_devices(simulation)
                self.events.put({"type": "operation_done", "operation": "refresh"})
            except Exception as exc:
                self.events.put({"type": "operation_error", "operation": "refresh", "message": str(exc)})

        threading.Thread(target=worker, name="enumerate-cameras", daemon=True).start()

    def _populate_devices(self, devices: list[CameraDescriptor]) -> None:
        self.devices = devices
        self.display_to_uid = {device.display_name: device.uid for device in devices}
        choices = (UNASSIGNED, *self.display_to_uid)
        selected_by_role: dict[str, str] = {}
        used: set[str] = set()
        # 先恢复所有仍然存在的序列号绑定，再把剩余设备依次补到空角色，
        # 防止单相机原本绑定为监控角色时被前面的触发角色抢占。
        for role in ROLES:
            saved_serial = self.app_config.role_serials.get(role, "")
            preferred = next((d.display_name for d in devices if d.serial == saved_serial and d.uid not in used), "")
            if preferred:
                selected_by_role[role] = preferred
                used.add(self.display_to_uid[preferred])
        for role in ROLES:
            if role not in selected_by_role:
                preferred = next((d.display_name for d in devices if d.uid not in used), "")
                if preferred:
                    selected_by_role[role] = preferred
                    used.add(self.display_to_uid[preferred])
            combo = self.role_combos[role]
            combo.configure(values=choices)
            self.role_selection[role].set(selected_by_role.get(role, UNASSIGNED))
        mode = "仿真" if self.simulation.get() else "实机"
        self.device_summary.set(f"设备：{len(devices)} 台（{mode}）")
        self.status.set(f"扫描完成：发现 {len(devices)} 台相机")
        self._log(f"扫描完成，发现 {len(devices)} 台相机（{mode}模式）", "success" if devices else "error")

    def _read_assignments(self) -> dict[str, str]:
        result = {}
        for role in ROLES:
            display = self.role_selection[role].get()
            if display == UNASSIGNED or not display:
                continue
            if display not in self.display_to_uid:
                raise ValueError(f"请选择{ROLE_NAMES[role]}")
            result[role] = self.display_to_uid[display]
        if not result:
            raise ValueError("至少需要为一个角色分配一台相机")
        return result

    def _read_settings(self, roles=ROLES) -> dict[str, CameraSettings]:
        settings: dict[str, CameraSettings] = {}
        for role in roles:
            values = self.settings_vars[role]
            try:
                item = CameraSettings(
                    auto_exposure=bool(values["auto_exposure"].get()),
                    exposure_ms=float(values["exposure_ms"].get()),
                    analog_gain=int(values["analog_gain"].get()),
                    trigger_edge=EDGE_NAMES.index(str(values["trigger_edge"].get())),
                    trigger_delay_us=int(values["trigger_delay_us"].get()),
                    trigger_jitter_us=int(values["trigger_jitter_us"].get()),
                )
                item.validate(role != MONITOR)
                settings[role] = item
            except (ValueError, TypeError) as exc:
                raise ValueError(f"{ROLE_NAMES[role]}参数错误：{exc}") from exc
        return settings

    def connect_devices(self) -> None:
        if self.busy or self.connected:
            return
        try:
            assignments = self._read_assignments()
            settings = self._read_settings(assignments)
        except ValueError as exc:
            messagebox.showerror("无法连接", str(exc))
            return
        self._set_busy(True, f"正在连接 {len(assignments)} 台相机……")

        def worker() -> None:
            try:
                self.system.connect(assignments, settings)
                self.events.put({"type": "operation_done", "operation": "connect"})
            except Exception as exc:
                self.events.put({"type": "operation_error", "operation": "connect", "message": str(exc)})

        threading.Thread(target=worker, name="connect-cameras", daemon=True).start()

    def disconnect_devices(self) -> None:
        if self.busy or not self.connected:
            return
        self._set_busy(True, "正在停止线程并断开相机……")

        def worker() -> None:
            try:
                self.system.disconnect()
                self.events.put({"type": "operation_done", "operation": "disconnect"})
            except Exception as exc:
                self.events.put({"type": "operation_error", "operation": "disconnect", "message": str(exc)})

        threading.Thread(target=worker, name="disconnect-cameras", daemon=True).start()

    def choose_output(self) -> None:
        selected = filedialog.askdirectory(initialdir=self.output_root.get() or str(self.root_dir))
        if selected:
            self.output_root.set(selected)

    def apply_settings(self) -> None:
        try:
            settings = self._read_settings(self.system.endpoints)
            self.system.apply_settings(settings)
            self.status.set("全部相机参数已应用")
        except Exception as exc:
            messagebox.showerror("参数应用失败", str(exc))

    def start_recording(self) -> None:
        try:
            if self.recording:
                return
            settings = self._read_settings(self.system.endpoints)
            self.system.apply_settings(settings)
            save_settings = SaveSettings(
                output_root=Path(self.output_root.get()),
                folder_name=self.folder_name.get().strip(),
                file_name=self.file_name.get().strip(),
                image_format=self.image_format.get(),
                jpeg_quality=int(self.jpeg_quality.get()) if self.save_enabled.get() else 92,
                save_enabled=self.save_enabled.get(),
            )
            session = self.system.start_recording(save_settings)
            self.recording = True
            self.session_status.set(f"正在采集：{session}" if session else "正在采集：不保存图片")
            self.status.set("触发相机正在等待外部时序信号")
            self._update_controls()
        except Exception as exc:
            messagebox.showerror("无法开始采集", str(exc))

    def stop_recording(self) -> None:
        if not self.recording or self.busy:
            return
        self._set_busy(True, "正在停止采集并写完保存队列……")

        def worker() -> None:
            try:
                self.system.stop_recording()
                self.events.put({"type": "operation_done", "operation": "stop_recording"})
            except Exception as exc:
                self.events.put({"type": "operation_error", "operation": "stop_recording", "message": str(exc)})

        threading.Thread(target=worker, name="stop-trigger-capture", daemon=True).start()

    def test_trigger(self, role: str | None) -> None:
        try:
            self.system.software_trigger(role)
            target = "A + B" if role is None else ROLE_NAMES[role]
            self.status.set(f"已向 {target} 发送测试触发")
        except Exception as exc:
            messagebox.showerror("测试触发失败", str(exc))

    def _set_busy(self, busy: bool, status: str | None = None) -> None:
        self.busy = busy
        if status:
            self.status.set(status)
        self._update_controls()

    def _update_save_controls(self) -> None:
        if not hasattr(self, "output_path_entry"):
            return
        enabled = self.save_enabled.get() and not self.busy and not self.recording
        state = "normal" if enabled else "disabled"
        self.output_path_entry.configure(state=state)
        self.output_browse_button.configure(state=state)
        self.folder_name_entry.configure(state=state)
        self.file_name_entry.configure(state=state)
        self.quality_entry.configure(state=state)
        self.format_combo.configure(state="readonly" if enabled else "disabled")
        self.save_check.configure(state="disabled" if self.busy or self.recording else "normal")

    def _update_controls(self) -> None:
        refresh_state = "disabled" if self.busy or self.connected else "normal"
        self.refresh_button.configure(state=refresh_state)
        self.connect_button.configure(state="normal" if not self.busy and not self.connected and len(self.devices) >= 1 else "disabled")
        self.disconnect_button.configure(state="normal" if not self.busy and self.connected else "disabled")
        self.apply_button.configure(state="normal" if not self.busy and self.connected and not self.recording else "disabled")
        trigger_a_connected = TRIGGER_A in self.system.endpoints
        trigger_b_connected = TRIGGER_B in self.system.endpoints
        any_trigger_connected = trigger_a_connected or trigger_b_connected
        self.start_button.configure(state="normal" if not self.busy and self.connected and not self.recording and any_trigger_connected else "disabled")
        self.stop_button.configure(state="normal" if not self.busy and self.recording else "disabled")
        self.trigger_all_button.configure(state="normal" if not self.busy and self.recording else "disabled")
        self.trigger_a_button.configure(state="normal" if not self.busy and self.recording and trigger_a_connected else "disabled")
        self.trigger_b_button.configure(state="normal" if not self.busy and self.recording and trigger_b_connected else "disabled")
        self.simulation_check.configure(state="disabled" if self.busy or self.connected else "normal")
        combo_state = "disabled" if self.busy or self.connected else "readonly"
        for combo in self.role_combos.values():
            combo.configure(state=combo_state)
        self._update_save_controls()

    def _set_preview(self, role: str, image: Image.Image) -> None:
        label = self.preview_labels[role]
        width = max(160, label.winfo_width() - 8)
        height = max(100, label.winfo_height() - 8)
        display = image.copy()
        # 实时显示优先使用低开销缩放；原始实验图像不受影响。
        display.thumbnail((width, height), Image.Resampling.BILINEAR)
        photo = ImageTk.PhotoImage(display)
        label.configure(image=photo, text="")
        self.preview_refs[role] = photo

    def _poll_events(self) -> None:
        latest_previews: dict[str, Image.Image] = {}
        latest_stats: dict[str, dict] = {}
        processed = 0
        try:
            # 一次最多处理有限数量事件，防止高频触发时 Tk 主线程长时间
            # 困在消息循环。预览和统计按角色合并，只渲染最新状态。
            while processed < 200:
                event = self.events.get_nowait()
                processed += 1
                event_type = event.get("type")
                if event_type == "devices":
                    self._populate_devices(event["devices"])
                elif event_type == "preview":
                    latest_previews[event["role"]] = event["image"]
                elif event_type in ("stats", "frame_saved"):
                    latest_stats[event["role"]] = event["stats"]
                elif event_type == "log":
                    self._log(event["message"], event.get("level", "info"))
                elif event_type == "connected":
                    self.connected = True
                    connected_count = len(event["assignments"])
                    role_text = "、".join(ROLE_NAMES[role] for role in ROLES if role in event["assignments"])
                    self.device_summary.set(f"设备：{connected_count} 台已连接")
                    self.status.set(f"已连接：{role_text}")
                    self._log(f"相机系统连接完成：{role_text}", "success")
                elif event_type == "disconnected":
                    self.connected = False
                    self.recording = False
                    self.device_summary.set("设备：已断开")
                    self.status.set("相机已断开")
                elif event_type == "recording_started":
                    if event.get("save_enabled"):
                        self._log(f"采集会话已建立：{event['path']}", "success")
                    else:
                        self._log("外触发采集已启动：仅预览，不保存图片", "success")
                elif event_type == "recording_stopped":
                    if event["path"]:
                        self._log(f"采集会话已停止：{event['path']}", "success")
                    else:
                        self._log("外触发采集已停止：本次未保存图片", "success")
                elif event_type == "camera_fault":
                    self._log(event["message"], "error")
                    messagebox.showerror("相机采集故障", event["message"])
                elif event_type == "operation_done":
                    operation = event["operation"]
                    self._set_busy(False)
                    if operation == "connect":
                        self.connected = True
                    elif operation == "disconnect":
                        self.connected = False
                        self.recording = False
                    elif operation == "stop_recording":
                        self.recording = False
                        if self.system.session_directory:
                            self.session_status.set(f"已完成：{self.system.session_directory}")
                        else:
                            self.session_status.set("已完成：本次未保存图片")
                        self.status.set("外触发采集已停止，监控相机继续运行")
                    self._update_controls()
                elif event_type == "operation_error":
                    operation_names = {
                        "refresh": "扫描相机",
                        "connect": "连接相机",
                        "disconnect": "断开相机",
                        "stop_recording": "停止采集",
                    }
                    self._set_busy(False, f"{operation_names.get(event['operation'], '操作')}失败")
                    self._log(event["message"], "error")
                    messagebox.showerror(f"{operation_names.get(event['operation'], '操作')}失败", event["message"])
        except queue.Empty:
            pass
        for role, image in latest_previews.items():
            self._set_preview(role, image)
        for role, stats in latest_stats.items():
            self.stats_vars[role].set(
                f"接收 {stats['received']}  ·  保存 {stats['saved']}  ·  "
                f"丢帧 {stats['dropped']}  ·  错误 {stats['errors']}"
            )
        if self.winfo_exists():
            self.after(10 if not self.events.empty() else 40, self._poll_events)

    def _save_user_config(self) -> None:
        settings = self._read_settings()
        try:
            jpeg_quality = int(self.jpeg_quality.get())
        except ValueError:
            jpeg_quality = 92
        role_serials: dict[str, str] = {}
        for role, selection in self.role_selection.items():
            uid = self.display_to_uid.get(selection.get())
            descriptor = next((item for item in self.devices if item.uid == uid), None)
            if descriptor:
                role_serials[role] = descriptor.serial
        try:
            output_path = Path(self.output_root.get()).resolve()
            output_value = str(output_path.relative_to(self.root_dir)) if output_path.is_relative_to(self.root_dir) else str(output_path)
        except (OSError, ValueError):
            output_value = self.output_root.get()
        config = AppConfig(
            simulation=self.simulation.get(),
            output_root=output_value,
            image_format=self.image_format.get(),
            jpeg_quality=jpeg_quality,
            save_enabled=self.save_enabled.get(),
            role_serials=role_serials,
            camera_settings={role: asdict(item) for role, item in settings.items()},
        )
        save_config(self.config_path, config)

    def _on_close(self) -> None:
        try:
            self._save_user_config()
        except Exception:
            pass
        try:
            self.system.close()
        finally:
            self.destroy()


def run() -> None:
    CameraApp().mainloop()
