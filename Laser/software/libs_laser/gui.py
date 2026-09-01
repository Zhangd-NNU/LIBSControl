from __future__ import annotations

import csv
import queue
import threading
from datetime import datetime
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .controller import ControllerEvent, LaserController, LaserError, LaserState
from .serial_port import SimulatedTransport, WindowsSerialTransport, list_serial_ports


class LaserApp(tk.Tk):
    MODE_LABELS = {
        "内时序（内部 CLK / Q）": "internal",
        "外时序（外部 CLK / Q）": "external_q",
        "外时序（外部 CLK，内部 Q）": "external_no_q",
    }
    STATE_MODE_LABELS = {
        "internal": "内时序",
        "external": "外时序",
        "external_q": "外时序 / 外部 Q",
        "external_no_q": "外时序 / 内部 Q",
    }

    def __init__(self) -> None:
        super().__init__()
        self.title("LIBS Laser Control · Dawa 激光器控制系统")
        self.geometry("1320x720")
        self.minsize(1080, 700)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.controller: LaserController | None = None
        self.events: queue.Queue = queue.Queue()
        self.log_rows: list[ControllerEvent] = []
        self.busy = False
        self._build_vars()
        self._build_styles()
        self._build_ui()
        self.refresh_ports()
        self.after_idle(self._fit_to_screen)
        self.after(60, self._poll_events)
        self._render_state(LaserState())

    def _build_vars(self) -> None:
        self.v = {
            "port": tk.StringVar(value="COM3"),
            "simulation": tk.BooleanVar(value=True),
            "voltage": tk.StringVar(value="680"),
            "frequency": tk.StringVar(value="20"),
            "divider": tk.StringVar(value="1"),
            "trigger_mode": tk.StringVar(value=next(iter(self.MODE_LABELS))),
            "check_water": tk.BooleanVar(value=False),
            "check_interlock": tk.BooleanVar(value=False),
            "check_optics": tk.BooleanVar(value=False),
        }
        self.status = tk.StringVar(value="未连接（建议先使用仿真模式验证流程）")
        self.device_info = tk.StringVar(value="OFFLINE")
        self.output_text = tk.StringVar(value="LASER OUTPUT  OFF")
        self.output_subtext = tk.StringVar(value="系统处于安全状态，尚未连接设备")
        self.safety_text = tk.StringVar(value="0 / 3  未就绪")
        self.state_hint = tk.StringVar(value="下一步：连接设备并完成联机")
        self.trigger_help = tk.StringVar(value="由激光器内部产生 CLK 与 Q 信号；内控时请拔掉外部触发线。")
        self.q_preview = tk.StringVar(value="预计 Q 输出：20 Hz")
        self.metric_vars = {
            "voltage": tk.StringVar(value="--- V"),
            "frequency": tk.StringVar(value="--- Hz"),
            "q_frequency": tk.StringVar(value="--- Hz"),
            "mode": tk.StringVar(value="内时序"),
        }

    def _build_styles(self) -> None:
        self.configure(bg="#05070a")
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TFrame", background="#05070a")
        style.configure("Header.TFrame", background="#05070a")
        style.configure("Panel.TFrame", background="#0b0f14")
        style.configure("Card.TFrame", background="#101820", relief="flat")
        style.configure("TLabel", background="#0b0f14", foreground="#d8e1e6", font=("Microsoft YaHei UI", 9))
        style.configure("Title.TLabel", background="#05070a", foreground="#46d9ff", font=("Microsoft YaHei UI", 18, "bold"))
        style.configure("SubTitle.TLabel", background="#05070a", foreground="#71818a", font=("Microsoft YaHei UI", 9))
        style.configure("Section.TLabel", background="#0b0f14", foreground="#46d9ff", font=("Microsoft YaHei UI", 10, "bold"))
        style.configure("Muted.TLabel", background="#0b0f14", foreground="#71818a", font=("Microsoft YaHei UI", 8))
        style.configure("CardMuted.TLabel", background="#101820", foreground="#82939c", font=("Microsoft YaHei UI", 8))
        style.configure("Status.TLabel", background="#080b0f", foreground="#8de8ff", padding=8)
        style.configure("MetricTitle.TLabel", background="#101820", foreground="#71818a", font=("Microsoft YaHei UI", 8), anchor="center")
        style.configure("MetricValue.TLabel", background="#101820", foreground="#e9f9ff", font=("Consolas", 11, "bold"), anchor="center")
        style.configure("OutputOff.TLabel", background="#101820", foreground="#7c8d96", font=("Segoe UI", 18, "bold"), anchor="center")
        style.configure("OutputOn.TLabel", background="#5a0b1a", foreground="#ff5a78", font=("Segoe UI", 18, "bold"), anchor="center")
        style.configure("OutputSubOff.TLabel", background="#101820", foreground="#71818a", font=("Microsoft YaHei UI", 9), anchor="center")
        style.configure("OutputSubOn.TLabel", background="#5a0b1a", foreground="#ffd5dd", font=("Microsoft YaHei UI", 9), anchor="center")
        style.configure("TLabelframe", background="#0b0f14", bordercolor="#26313a", relief="solid")
        style.configure("TLabelframe.Label", background="#0b0f14", foreground="#46d9ff", font=("Microsoft YaHei UI", 10, "bold"))
        style.configure("TEntry", fieldbackground="#0f151b", foreground="#f0f6f8", bordercolor="#34414b", insertcolor="#46d9ff", padding=4)
        style.configure("TCombobox", fieldbackground="#0f151b", foreground="#f0f6f8", bordercolor="#34414b", arrowcolor="#46d9ff", padding=4)
        style.map("TCombobox", fieldbackground=[("readonly", "#0f151b")], foreground=[("readonly", "#f0f6f8")], selectbackground=[("readonly", "#0f151b")], selectforeground=[("readonly", "#f0f6f8")])
        self.option_add("*TCombobox*Listbox.background", "#0f151b")
        self.option_add("*TCombobox*Listbox.foreground", "#f0f6f8")
        self.option_add("*TCombobox*Listbox.selectBackground", "#007f9f")
        style.configure("TCheckbutton", background="#0b0f14", foreground="#c3cdd2", padding=(0, 2))
        style.map("TCheckbutton", background=[("active", "#0b0f14")], foreground=[("disabled", "#536069")])
        style.configure("TButton", background="#202830", foreground="#e8eef1", borderwidth=0, padding=(8, 4), font=("Microsoft YaHei UI", 9))
        style.map("TButton", background=[("active", "#33404a"), ("pressed", "#141a20"), ("disabled", "#141a20")], foreground=[("disabled", "#56636b")])
        style.configure("Accent.TButton", background="#08a9d2", foreground="#03121b", font=("Microsoft YaHei UI", 9, "bold"), padding=(8, 4))
        style.map("Accent.TButton", background=[("disabled", "#12333c"), ("active", "#38d5ff"), ("pressed", "#0086aa")], foreground=[("disabled", "#4c6870")])
        style.configure("Laser.TButton", background="#b83b59", foreground="white", font=("Microsoft YaHei UI", 9, "bold"), padding=(8, 5))
        style.map("Laser.TButton", background=[("disabled", "#2b1820"), ("active", "#e34f6b"), ("pressed", "#8f2940")], foreground=[("disabled", "#704652")])
        style.configure("Safe.TButton", background="#14735d", foreground="white", font=("Microsoft YaHei UI", 9, "bold"), padding=(8, 5))
        style.map("Safe.TButton", background=[("disabled", "#142d28"), ("active", "#1aa681"), ("pressed", "#0c5945")], foreground=[("disabled", "#48645d")])
        style.configure("BadgeOff.TLabel", background="#192027", foreground="#829099", font=("Segoe UI", 9, "bold"), padding=(10, 5), anchor="center")
        style.configure("BadgeOn.TLabel", background="#123e34", foreground="#54e1b2", font=("Segoe UI", 9, "bold"), padding=(10, 5), anchor="center")
        style.configure("BadgeWarn.TLabel", background="#493216", foreground="#ffc268", font=("Segoe UI", 9, "bold"), padding=(10, 5), anchor="center")
        style.configure("StageOff.TLabel", background="#141b21", foreground="#66747c", font=("Microsoft YaHei UI", 9, "bold"), padding=(8, 7), anchor="center")
        style.configure("StageOn.TLabel", background="#123c48", foreground="#62e5ff", font=("Microsoft YaHei UI", 9, "bold"), padding=(8, 7), anchor="center")
        style.configure("StageWarm.TLabel", background="#493216", foreground="#ffc268", font=("Microsoft YaHei UI", 9, "bold"), padding=(8, 7), anchor="center")
        style.configure("StageDanger.TLabel", background="#5a0b1a", foreground="#ff5a78", font=("Microsoft YaHei UI", 9, "bold"), padding=(8, 7), anchor="center")
        style.configure("Arrow.TLabel", background="#0b0f14", foreground="#3f505a", font=("Segoe UI", 11, "bold"), anchor="center")
        style.configure("Safety.Horizontal.TProgressbar", troughcolor="#182027", background="#1fbe91", bordercolor="#182027", lightcolor="#1fbe91", darkcolor="#1fbe91")
        style.configure("Log.Treeview", background="#070a0d", fieldbackground="#070a0d", foreground="#bfcbd1", rowheight=23, bordercolor="#26313a", font=("Consolas", 9))
        style.configure("Log.Treeview.Heading", background="#151d23", foreground="#46d9ff", font=("Microsoft YaHei UI", 9, "bold"))
        style.map("Log.Treeview", background=[("selected", "#164b59")])

    def _build_ui(self) -> None:
        self.columnconfigure(0, minsize=270)
        self.columnconfigure(1, weight=1, minsize=430)
        self.columnconfigure(2, minsize=295)
        self.rowconfigure(1, weight=1)

        header = ttk.Frame(self, style="Header.TFrame", padding=(20, 14, 20, 11))
        header.grid(row=0, column=0, columnspan=3, sticky="ew")
        title_block = ttk.Frame(header, style="Header.TFrame")
        title_block.pack(side="left")
        ttk.Label(title_block, text="LIBS LASER CONTROL", style="Title.TLabel").pack(anchor="w")
        ttk.Label(title_block, text="Dawa 固体激光器  ·  RS232 CONTROL CONSOLE", style="SubTitle.TLabel").pack(anchor="w", pady=(1, 0))
        self.device_badge = ttk.Label(header, textvariable=self.device_info, style="BadgeOff.TLabel")
        self.device_badge.pack(side="right", pady=5)

        left = ttk.Frame(self, style="Panel.TFrame", padding=12)
        center = ttk.Frame(self, style="Panel.TFrame", padding=12)
        right = ttk.Frame(self, style="Panel.TFrame", padding=12)
        left.grid(row=1, column=0, sticky="nsew", padx=(12, 6))
        center.grid(row=1, column=1, sticky="nsew", padx=6)
        right.grid(row=1, column=2, sticky="nsew", padx=(6, 12))

        conn = ttk.LabelFrame(left, text=" 01  设备连接 ", padding=10)
        conn.pack(fill="x", pady=(0, 10))
        conn.columnconfigure(1, weight=1)
        ttk.Label(conn, text="串口端口").grid(row=0, column=0, sticky="w", padx=(0, 7), pady=3)
        self.port_combo = ttk.Combobox(conn, textvariable=self.v["port"], width=15)
        self.port_combo.grid(row=0, column=1, sticky="ew", pady=3)
        self.refresh_button = ttk.Button(conn, text="刷新", command=self.refresh_ports, width=6)
        self.refresh_button.grid(row=0, column=2, padx=(5, 0))
        ttk.Label(conn, text="通讯协议").grid(row=1, column=0, sticky="w", pady=3)
        ttk.Label(conn, text="19200  ·  8N1  ·  HEX", style="Muted.TLabel").grid(row=1, column=1, columnspan=2, sticky="w")
        self.simulation_check = ttk.Checkbutton(conn, text="仿真模式（不连接真实激光器）", variable=self.v["simulation"])
        self.simulation_check.grid(row=2, column=0, columnspan=3, sticky="w", pady=(7, 2))
        self.connect_button = ttk.Button(conn, text="连接并联机", command=self.connect, style="Accent.TButton")
        self.connect_button.grid(row=3, column=0, columnspan=2, sticky="ew", padx=(0, 3), pady=(7, 0))
        self.disconnect_button = ttk.Button(conn, text="安全断开", command=self.disconnect)
        self.disconnect_button.grid(row=3, column=2, sticky="ew", padx=(3, 0), pady=(7, 0))

        safety = ttk.LabelFrame(left, text=" 02  出光安全确认 ", padding=10)
        safety.pack(fill="x", pady=(0, 10))
        safety.columnconfigure(0, weight=1)
        ttk.Label(safety, text="完成全部检查后才允许启动", style="Muted.TLabel").grid(row=0, column=0, sticky="w")
        self.safety_badge = ttk.Label(safety, textvariable=self.safety_text, style="BadgeWarn.TLabel")
        self.safety_badge.grid(row=0, column=1, sticky="e")
        self.safety_progress = ttk.Progressbar(safety, maximum=3, value=0, style="Safety.Horizontal.TProgressbar")
        self.safety_progress.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(7, 7))
        checks = (
            ("冷却水循环正常、水量充足", "check_water"),
            ("安全联锁闭合、急停可用", "check_interlock"),
            ("光路封闭且人员佩戴防护镜", "check_optics"),
        )
        for row, (label, key) in enumerate(checks, 2):
            ttk.Checkbutton(safety, text=label, variable=self.v[key], command=self._safety_changed).grid(row=row, column=0, columnspan=2, sticky="w")
        ttk.Label(safety, text="注意：软件控制不能替代硬件急停与安全联锁", foreground="#e9a34b", wraplength=255).grid(row=5, column=0, columnspan=2, sticky="w", pady=(7, 0))

        fault = ttk.LabelFrame(left, text=" 03  设备联锁状态 ", padding=10)
        fault.pack(fill="x", pady=(0, 10))
        self.water_fault_label = ttk.Label(fault, text="● 水流状态：未上报", foreground="#65747b")
        self.water_fault_label.pack(anchor="w", pady=3)
        self.door_fault_label = ttk.Label(fault, text="● 门联锁：未上报", foreground="#65747b")
        self.door_fault_label.pack(anchor="w", pady=3)
        ttk.Label(fault, text="收到故障上传后，本次连接内锁定出光；排障后重新连接。", foreground="#77878f", wraplength=245).pack(anchor="w", pady=(6, 0))

        center.columnconfigure(0, weight=1)
        center.rowconfigure(4, weight=0)

        output_card = ttk.Frame(center, style="Card.TFrame", padding=(10, 12))
        output_card.grid(row=0, column=0, sticky="ew", pady=(0, 9))
        self.output_label = ttk.Label(output_card, textvariable=self.output_text, style="OutputOff.TLabel")
        self.output_label.pack(fill="x")
        self.output_sub_label = ttk.Label(output_card, textvariable=self.output_subtext, style="OutputSubOff.TLabel")
        self.output_sub_label.pack(fill="x", pady=(3, 0))

        metrics = ttk.Frame(center, style="Panel.TFrame")
        metrics.grid(row=1, column=0, sticky="ew", pady=(0, 9))
        metrics.columnconfigure((0, 1, 2, 3), weight=1, uniform="metric")
        self._metric_card(metrics, 0, "本振电压", self.metric_vars["voltage"])
        self._metric_card(metrics, 1, "灯频率", self.metric_vars["frequency"])
        self._metric_card(metrics, 2, "Q 输出频率", self.metric_vars["q_frequency"])
        self._metric_card(metrics, 3, "时序模式", self.metric_vars["mode"])

        state_frame = ttk.LabelFrame(center, text=" 实时状态链 ", padding=(10, 8))
        state_frame.grid(row=2, column=0, sticky="ew", pady=(0, 9))
        chain = ttk.Frame(state_frame, style="Panel.TFrame")
        chain.pack(fill="x")
        self.stage_labels = {}
        stage_names = (("serial", "串口"), ("online", "联机"), ("prefire", "预燃"), ("work", "工作"), ("q", "Q 输出"))
        for index, (key, text) in enumerate(stage_names):
            column = index * 2
            chain.columnconfigure(column, weight=1, uniform="stage")
            label = ttk.Label(chain, text=text, style="StageOff.TLabel")
            label.grid(row=0, column=column, sticky="ew")
            self.stage_labels[key] = label
            if index < len(stage_names) - 1:
                ttk.Label(chain, text="›", style="Arrow.TLabel", width=2).grid(row=0, column=column + 1)

        sequence = ttk.LabelFrame(center, text=" 启停控制 ", padding=10)
        sequence.grid(row=3, column=0, sticky="ew", pady=(0, 9))
        sequence.columnconfigure((0, 1, 2), weight=1, uniform="sequence")
        self.prefire_on = ttk.Button(sequence, text="1  开启预燃", command=lambda: self._safety_action("开启预燃", lambda: self.controller.set_prefire(True)), style="Accent.TButton")
        self.prefire_off = ttk.Button(sequence, text="关闭预燃", command=lambda: self._run_action("关闭预燃", lambda: self.controller.set_prefire(False)))
        self.work_on = ttk.Button(sequence, text="2  开启工作", command=lambda: self._safety_action("开启工作", lambda: self.controller.set_work(True)), style="Accent.TButton")
        self.work_off = ttk.Button(sequence, text="停止工作", command=lambda: self._run_action("停止工作", lambda: self.controller.set_work(False)))
        self.q_on = ttk.Button(sequence, text="3  开 Q / 出光", command=self.enable_q, style="Laser.TButton")
        self.q_off = ttk.Button(sequence, text="关闭 Q", command=lambda: self._run_action("关闭 Q 输出", lambda: self.controller.set_q(False)), style="Safe.TButton")
        self.prefire_on.grid(row=0, column=0, sticky="ew", padx=(0, 3), pady=3)
        self.work_on.grid(row=0, column=1, sticky="ew", padx=3, pady=3)
        self.q_on.grid(row=0, column=2, sticky="ew", padx=(3, 0), pady=3)
        self.prefire_off.grid(row=1, column=0, sticky="ew", padx=(0, 3), pady=3)
        self.work_off.grid(row=1, column=1, sticky="ew", padx=3, pady=3)
        self.q_off.grid(row=1, column=2, sticky="ew", padx=(3, 0), pady=3)
        self.stop_button = ttk.Button(sequence, text="安全停机  ·  关 Q → 停工作 → 关预燃", command=self.emergency_stop, style="Laser.TButton")
        self.stop_button.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(7, 2))

        log_frame = ttk.LabelFrame(center, text=" 协议监视器 ", padding=6)
        log_frame.grid(row=4, column=0, sticky="ew")
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)
        self.log = ttk.Treeview(
            log_frame,
            columns=("time", "dir", "message", "hex"),
            show="headings",
            height=3,
            style="Log.Treeview",
        )
        headings = (("time", "时间", 65), ("dir", "方向", 42), ("message", "事件", 105), ("hex", "HEX 帧", 158))
        for key, label, width in headings:
            self.log.heading(key, text=label)
            self.log.column(key, width=width, minwidth=40, stretch=key in ("message", "hex"))
        scroll = ttk.Scrollbar(log_frame, orient="vertical", command=self.log.yview)
        self.log.configure(yscrollcommand=scroll.set)
        self.log.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")

        params = ttk.LabelFrame(right, text=" 01  激光参数 ", padding=8)
        params.pack(fill="x", pady=(0, 10))
        params.columnconfigure(1, weight=1)
        self._entry(params, "本振电压", "voltage", "V", 0)
        self._entry(params, "灯频率", "frequency", "Hz", 1)
        self._entry(params, "分频系数", "divider", "0=单次", 2)
        self.apply_button = ttk.Button(params, text="应用全部参数", command=self.apply_parameters, style="Accent.TButton")
        self.apply_button.grid(row=3, column=0, columnspan=3, sticky="ew", pady=(6, 0))
        ttk.Label(params, textvariable=self.q_preview, foreground="#53d9b0").grid(row=4, column=0, columnspan=3, sticky="w", pady=(6, 0))
        ttk.Label(params, text="范围 1-1000 V；频率不得超过设备额定值", style="Muted.TLabel", wraplength=255).grid(row=5, column=0, columnspan=3, sticky="w", pady=(2, 0))

        trigger = ttk.LabelFrame(right, text=" 02  触发与时序 ", padding=8)
        trigger.pack(fill="x", pady=(0, 10))
        trigger.columnconfigure(0, weight=1)
        self.trigger_combo = ttk.Combobox(trigger, textvariable=self.v["trigger_mode"], values=tuple(self.MODE_LABELS), state="readonly")
        self.trigger_combo.grid(row=0, column=0, sticky="ew")
        self.trigger_combo.bind("<<ComboboxSelected>>", self._trigger_selected)
        self.trigger_button = ttk.Button(trigger, text="应用触发方式", command=self.apply_trigger)
        self.trigger_button.grid(row=1, column=0, sticky="ew", pady=(6, 0))
        ttk.Label(trigger, textvariable=self.trigger_help, style="Muted.TLabel", wraplength=255).grid(row=2, column=0, sticky="w", pady=(6, 0))
        ttk.Label(trigger, text="外触发：TTL 5 V · 50 Ω · 带载 ≥50 mA", foreground="#e9a34b", wraplength=255).grid(row=3, column=0, sticky="w", pady=(4, 0))

        single = ttk.LabelFrame(right, text=" 03  单次触发 ", padding=8)
        single.pack(fill="x", pady=(0, 10))
        single.columnconfigure((0, 1), weight=1, uniform="single")
        self.single_on = ttk.Button(single, text="进入单次模式", command=lambda: self._run_action("进入单次模式", lambda: self.controller.set_single_mode(True)))
        self.single_off = ttk.Button(single, text="退出单次模式", command=lambda: self._run_action("退出单次模式", lambda: self.controller.set_single_mode(False)))
        self.single_fire = ttk.Button(single, text="单次激光触发", command=self.single_trigger, style="Laser.TButton")
        self.single_on.grid(row=0, column=0, sticky="ew", padx=(0, 3))
        self.single_off.grid(row=0, column=1, sticky="ew", padx=(3, 0))
        self.single_fire.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(5, 0))
        ttk.Label(single, text="单次模式会设置分频=0；触发前需开启预燃和工作", style="Muted.TLabel", wraplength=255).grid(row=2, column=0, columnspan=2, sticky="w", pady=(4, 0))

        statusbar = ttk.Frame(self, style="Panel.TFrame")
        statusbar.grid(row=2, column=0, columnspan=3, sticky="ew", padx=12, pady=(8, 10))
        ttk.Label(statusbar, text="● SYSTEM", style="Status.TLabel").pack(side="left")
        ttk.Label(statusbar, textvariable=self.status, style="Status.TLabel").pack(side="left", fill="x", expand=True)
        ttk.Button(statusbar, text="清空日志", command=self.clear_log).pack(side="right", padx=(5, 7), pady=4)
        ttk.Button(statusbar, text="导出日志", command=self.export_log).pack(side="right", pady=4)

        self.control_buttons = [
            self.disconnect_button, self.prefire_on, self.prefire_off, self.work_on, self.work_off,
            self.q_on, self.q_off, self.stop_button, self.apply_button, self.trigger_button,
            self.single_on, self.single_off, self.single_fire,
        ]

        for key in ("frequency", "divider"):
            self.v[key].trace_add("write", self._update_q_preview)
        self._safety_changed()

    def _entry(self, parent, label: str, key: str, unit: str, row: int) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=2)
        ttk.Entry(parent, textvariable=self.v[key], width=12).grid(row=row, column=1, sticky="ew", padx=(7, 5), pady=2)
        ttk.Label(parent, text=unit, foreground="#77878f").grid(row=row, column=2, sticky="w", pady=2)

    def _metric_card(self, parent, column: int, title: str, variable: tk.StringVar) -> None:
        card = ttk.Frame(parent, style="Card.TFrame", padding=(5, 6))
        card.grid(row=0, column=column, sticky="nsew", padx=(0 if column == 0 else 3, 0 if column == 3 else 3))
        ttk.Label(card, text=title, style="MetricTitle.TLabel").pack(fill="x")
        ttk.Label(card, textvariable=variable, style="MetricValue.TLabel").pack(fill="x", pady=(3, 0))

    def _safety_changed(self, *_args) -> None:
        count = sum(self.v[key].get() for key in ("check_water", "check_interlock", "check_optics"))
        self.safety_progress.configure(value=count)
        if count == 3:
            self.safety_text.set("3 / 3  READY")
            self.safety_badge.configure(style="BadgeOn.TLabel")
        else:
            self.safety_text.set(f"{count} / 3  未就绪")
            self.safety_badge.configure(style="BadgeWarn.TLabel")
        if hasattr(self, "stage_labels"):
            state = self.controller.state if self.controller else LaserState()
            self._render_state(state)
        elif hasattr(self, "connect_button"):
            self._update_controls()

    def _trigger_selected(self, _event=None) -> None:
        mode = self.MODE_LABELS[self.v["trigger_mode"].get()]
        help_text = {
            "internal": "激光器内部产生 CLK 与 Q 信号；内控时请拔掉外部触发线。",
            "external_q": "外部同时提供 CLK IN 与 Q IN；灯-Q 延迟由外部时序器设置，软件不发送开 Q 命令。",
            "external_no_q": "外部只提供 CLK IN；Q 信号由激光器内部延时产生，启动后仍需软件开启 Q。",
        }
        self.trigger_help.set(help_text[mode])

    def _update_q_preview(self, *_args) -> None:
        try:
            frequency = int(self.v["frequency"].get())
            divider = int(self.v["divider"].get())
            if divider == 0:
                text = "预计 Q 输出：单次触发"
            elif frequency > 0 and divider > 0:
                text = f"预计 Q 输出：{frequency / divider:g} Hz"
            else:
                text = "预计 Q 输出：参数无效"
        except ValueError:
            text = "预计 Q 输出：参数无效"
        self.q_preview.set(text)

    def _fit_to_screen(self) -> None:
        self.update_idletasks()
        screen_w, screen_h = self.winfo_screenwidth(), self.winfo_screenheight()
        width = max(1080, min(1320, screen_w - 40))
        height = 720
        self.geometry(f"{width}x{height}+{max(0, (screen_w-width)//2)}+{max(0, (screen_h-height)//2-12)}")

    def refresh_ports(self) -> None:
        ports = list_serial_ports()
        self.port_combo.configure(values=ports)
        if ports and self.v["port"].get() not in ports:
            self.v["port"].set(ports[0])
        self.status.set(f"检测到 {len(ports)} 个串口" if ports else "未检测到串口；可使用仿真模式")

    def _controller_event(self, event: ControllerEvent) -> None:
        self.events.put(("controller_event", event))

    def _state_event(self, state: LaserState) -> None:
        self.events.put(("state", state))

    def connect(self) -> None:
        if self.controller and self.controller.state.serial_connected:
            messagebox.showinfo("已经连接", "当前串口已经连接。")
            return
        simulation = self.v["simulation"].get()
        transport = SimulatedTransport() if simulation else WindowsSerialTransport(self.v["port"].get(), 19200)
        self.controller = LaserController(transport, event_callback=self._controller_event, state_callback=self._state_event)

        def action() -> None:
            self.controller.connect()
            self.controller.set_online(True)

        self._run_action("连接并联机", action)

    def disconnect(self) -> None:
        if not self.controller:
            return
        self._run_action("安全断开", lambda: self.controller.disconnect(safe=True))

    def _safety_ready(self) -> bool:
        return all(self.v[key].get() for key in ("check_water", "check_interlock", "check_optics"))

    def _safety_action(self, name: str, function) -> None:
        if not self._safety_ready():
            messagebox.showwarning("安全确认未完成", "请先逐项确认冷却水、安全联锁和光路防护。")
            return
        self._run_action(name, function)

    def enable_q(self) -> None:
        if not self._safety_ready():
            messagebox.showwarning("禁止出光", "请先完成全部出光安全确认。")
            return
        if not messagebox.askyesno("确认开启激光输出", "开启 Q 后设备将产生危险激光。\n确认光路封闭、人员防护和联锁均正常？", icon="warning"):
            return
        self._run_action("开启 Q 输出", lambda: self.controller.set_q(True))

    def single_trigger(self) -> None:
        if not self._safety_ready():
            messagebox.showwarning("禁止单次触发", "请先完成全部出光安全确认。")
            return
        if not messagebox.askyesno("确认单次触发", "将立即发射一次激光脉冲，确认继续？", icon="warning"):
            return
        self._run_action("单次激光触发", self.controller.single_trigger)

    def emergency_stop(self) -> None:
        if not self.controller or not self.controller.state.serial_connected:
            return
        try:
            self.controller.emergency_stop()
            self.status.set("已下发安全停机序列")
        except Exception as exc:
            self.status.set(str(exc))
            messagebox.showerror("安全停机发送异常", str(exc))

    def apply_parameters(self) -> None:
        try:
            voltage = int(self.v["voltage"].get())
            frequency = int(self.v["frequency"].get())
            divider = int(self.v["divider"].get())
        except ValueError:
            messagebox.showerror("参数错误", "电压、频率和分频系数必须是整数。")
            return
        self._run_action("应用激光参数", lambda: self.controller.apply_parameters(voltage, frequency, divider))

    def apply_trigger(self) -> None:
        mode = self.MODE_LABELS[self.v["trigger_mode"].get()]
        self._run_action("应用触发方式", lambda: self.controller.set_trigger_mode(mode))

    def _run_action(self, name: str, function) -> None:
        if self.busy:
            messagebox.showinfo("操作进行中", "请等待当前串口操作完成。")
            return
        if not self.controller and name != "连接并联机":
            messagebox.showwarning("未连接", "请先连接激光器。")
            return
        self.busy = True
        self.status.set(f"{name}……")
        self._update_controls()

        def worker() -> None:
            try:
                function()
                self.events.put(("done", name))
            except Exception as exc:
                self.events.put(("error", name, str(exc)))

        threading.Thread(target=worker, name="laser-command", daemon=True).start()

    def _poll_events(self) -> None:
        try:
            while True:
                event = self.events.get_nowait()
                if event[0] == "controller_event":
                    self._append_log(event[1])
                elif event[0] == "state":
                    self._render_state(event[1])
                elif event[0] == "done":
                    self.busy = False
                    self.status.set(f"{event[1]}完成")
                    if event[1] == "安全断开":
                        for key in ("check_water", "check_interlock", "check_optics"):
                            self.v[key].set(False)
                        self._safety_changed()
                    self._update_controls()
                elif event[0] == "error":
                    self.busy = False
                    self.status.set(f"{event[1]}失败：{event[2]}")
                    self._update_controls()
                    messagebox.showerror(f"{event[1]}失败", event[2])
        except queue.Empty:
            pass
        self.after(60, self._poll_events)

    def _render_state(self, state: LaserState) -> None:
        mode = "仿真" if self.v["simulation"].get() else "实机"
        if state.serial_connected:
            link = "已联机" if state.online else "串口已开 / 未联机"
            self.device_info.set(f"{mode}  ·  {link}")
            self.device_badge.configure(style="BadgeOn.TLabel" if state.online else "BadgeWarn.TLabel")
        else:
            self.device_info.set("OFFLINE")
            self.device_badge.configure(style="BadgeOff.TLabel")
        voltage = "---" if state.voltage_v is None else str(state.voltage_v)
        frequency = "---" if state.frequency_hz is None else str(state.frequency_hz)
        output_frequency = state.output_frequency_hz
        q_frequency = "--- Hz" if output_frequency is None else ("SINGLE" if state.divider == 0 else f"{output_frequency:g} Hz")
        mode_text = self.STATE_MODE_LABELS.get(state.trigger_mode, state.trigger_mode)
        self.metric_vars["voltage"].set(f"{voltage} V")
        self.metric_vars["frequency"].set(f"{frequency} Hz")
        self.metric_vars["q_frequency"].set(q_frequency)
        self.metric_vars["mode"].set(mode_text)

        fault = state.water_fault or state.door_fault
        if state.output_active:
            self.output_text.set("⚠  LASER OUTPUT  ACTIVE")
            self.output_subtext.set("危险激光正在输出  ·  需要停止时优先关闭 Q 或执行安全停机")
            self.output_label.configure(style="OutputOn.TLabel")
            self.output_sub_label.configure(style="OutputSubOn.TLabel")
        elif state.trigger_mode == "external_q" and state.prefire and state.working:
            self.output_text.set("⚠  EXTERNAL Q ARMED  ·  OUTPUT FOLLOWS TTL")
            self.output_subtext.set("外部 Q 输入已具备出光条件，激光输出取决于外部 TTL 信号")
            self.output_label.configure(style="OutputOn.TLabel")
            self.output_sub_label.configure(style="OutputSubOn.TLabel")
        else:
            self.output_text.set("LASER OUTPUT  OFF")
            if fault:
                self.output_subtext.set("检测到设备联锁故障，启动操作已锁定")
            elif state.working:
                self.output_subtext.set("氙灯正在工作，Q 开关尚未开启")
            elif state.prefire:
                self.output_subtext.set("预燃已开启，激光器尚未进入工作状态")
            elif state.online:
                self.output_subtext.set("设备已联机，可在停止状态下设置参数")
            elif state.serial_connected:
                self.output_subtext.set("串口已连接，等待设备联机确认")
            else:
                self.output_subtext.set("系统处于安全状态，尚未连接设备")
            self.output_label.configure(style="OutputOff.TLabel")
            self.output_sub_label.configure(style="OutputSubOff.TLabel")

        stage_states = {
            "serial": (state.serial_connected, "StageOn.TLabel"),
            "online": (state.online, "StageOn.TLabel"),
            "prefire": (state.prefire, "StageWarm.TLabel"),
            "work": (state.working, "StageWarm.TLabel"),
            "q": (state.q_enabled or (state.trigger_mode == "external_q" and state.working), "StageDanger.TLabel"),
        }
        for key, (active, active_style) in stage_states.items():
            self.stage_labels[key].configure(style=active_style if active else "StageOff.TLabel")
        self.stage_labels["q"].configure(text="外部 Q" if state.trigger_mode == "external_q" else "Q 输出")

        if fault:
            hint = "联锁故障：已自动发送安全停机序列，排障后请重新连接"
        elif not state.serial_connected:
            hint = "下一步：选择串口并连接设备"
        elif not state.online:
            hint = "下一步：确认手控盒已进入 PC 模式并重新联机"
        elif not self._safety_ready():
            hint = "下一步：设置参数，并完成左侧 3 项安全确认"
        elif not state.prefire:
            hint = "下一步：确认参数后开启预燃"
        elif not state.working:
            hint = "下一步：开启工作"
        elif state.trigger_mode == "external_q":
            hint = "外部 Q 已武装：输出由 CLK IN / Q IN 的 TTL 信号控制"
        elif state.single_mode:
            hint = "单次模式已就绪：使用右侧“单次激光触发”"
        elif not state.q_enabled:
            hint = "下一步：确认光路安全后开启 Q 输出"
        else:
            hint = "激光输出中：结束时关闭 Q，或执行安全停机"
        self.state_hint.set(hint)

        self.water_fault_label.configure(
            text="● 水流状态：故障锁定" if state.water_fault else "● 水流状态：未见故障上传",
            foreground="#ff4868" if state.water_fault else "#47c99a",
        )
        self.door_fault_label.configure(
            text="● 门联锁：故障锁定" if state.door_fault else "● 门联锁：未见故障上传",
            foreground="#ff4868" if state.door_fault else "#47c99a",
        )
        if state.voltage_v is not None:
            self.v["voltage"].set(str(state.voltage_v))
        if state.frequency_hz is not None:
            self.v["frequency"].set(str(state.frequency_hz))
        if state.divider is not None:
            self.v["divider"].set(str(state.divider))
        self._update_controls(state)

    def _update_controls(self, state: LaserState | None = None) -> None:
        state = state or (self.controller.state if self.controller else LaserState())
        connected = state.serial_connected
        online = connected and state.online
        idle = online and not state.working and not state.q_enabled
        safety = self._safety_ready()
        fault = state.water_fault or state.door_fault
        normal = "disabled" if self.busy else "normal"
        self.connect_button.configure(state="disabled" if self.busy or connected else "normal")
        self.disconnect_button.configure(state=normal if connected else "disabled")
        connection_input_state = "disabled" if self.busy or connected else "normal"
        self.port_combo.configure(state=connection_input_state)
        self.refresh_button.configure(state=connection_input_state)
        self.simulation_check.configure(state=connection_input_state)
        self.prefire_on.configure(state=normal if online and safety and not state.prefire and not fault else "disabled")
        self.prefire_off.configure(state=normal if online and state.prefire else "disabled")
        self.work_on.configure(state=normal if online and safety and state.prefire and not state.working and not fault else "disabled")
        self.work_off.configure(state=normal if online and state.working else "disabled")
        q_can_enable = state.trigger_mode != "external_q" and not state.single_mode
        self.q_on.configure(state=normal if online and safety and state.prefire and state.working and q_can_enable and not state.q_enabled and not fault else "disabled")
        self.q_off.configure(state=normal if online and state.q_enabled else "disabled")
        self.stop_button.configure(state=normal if connected else "disabled")
        self.apply_button.configure(state=normal if idle and not fault else "disabled")
        self.trigger_button.configure(state=normal if idle and not fault else "disabled")
        self.single_on.configure(state=normal if idle and not state.single_mode and not fault else "disabled")
        self.single_off.configure(state=normal if idle and state.single_mode else "disabled")
        self.single_fire.configure(state=normal if online and safety and state.single_mode and state.prefire and state.working and not fault else "disabled")

    def _append_log(self, event: ControllerEvent) -> None:
        self.log_rows.append(event)
        clock = datetime.fromtimestamp(event.timestamp).strftime("%H:%M:%S.%f")[:-3]
        raw = event.raw.hex(" ").upper() if event.raw else ""
        tags = (event.direction,)
        self.log.insert("", "end", values=(clock, event.direction, event.message, raw), tags=tags)
        self.log.tag_configure("TX", foreground="#40dcff")
        self.log.tag_configure("RX", foreground="#52e0a8")
        self.log.tag_configure("WARN", foreground="#ffad4d")
        self.log.tag_configure("SYS", foreground="#9aaab1")
        self.log.yview_moveto(1.0)

    def export_log(self) -> None:
        if not self.log_rows:
            messagebox.showinfo("没有日志", "当前没有可导出的协议日志。")
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".csv",
            initialfile=f"laser_protocol_{datetime.now():%Y%m%d_%H%M%S}.csv",
            filetypes=[("CSV", "*.csv")],
        )
        if not path:
            return
        with open(path, "w", newline="", encoding="utf-8-sig") as stream:
            writer = csv.writer(stream)
            writer.writerow(("timestamp", "direction", "message", "hex"))
            for event in self.log_rows:
                writer.writerow((datetime.fromtimestamp(event.timestamp).isoformat(timespec="milliseconds"), event.direction, event.message, event.raw.hex(" ").upper()))
        self.status.set(f"日志已导出：{path}")

    def clear_log(self) -> None:
        self.log_rows.clear()
        self.log.delete(*self.log.get_children())

    def _on_close(self) -> None:
        try:
            if self.controller and self.controller.state.serial_connected:
                self.controller.emergency_stop()
                self.controller.disconnect(safe=False)
        finally:
            self.destroy()


def run() -> None:
    LaserApp().mainloop()
