from __future__ import annotations

import json
import queue
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .controller import LogEntry, TimingController, TimingControllerError, available_ports
from .protocol import Mode, TimingParameters, build_parameter_frame, frame_hex


class TimingApp(tk.Tk):
    BG, PANEL, FIELD = "#0b1016", "#121a23", "#192430"
    CYAN, GREEN, TEXT, MUTED = "#63c7e8", "#45d6a5", "#e7edf3", "#81909d"

    def __init__(self):
        super().__init__()
        self.title("LIBS Timing Control · 四通道时序控制器")
        self.geometry("1080x660")
        self.minsize(980, 560)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.events: queue.Queue = queue.Queue()
        self.controller = TimingController(self._enqueue_log)
        self._completion_job = None
        self._build_vars()
        self._configure_style()
        self._build_redesigned_ui()
        self._refresh_ports()
        self._update_mode()
        self._update_preview()
        self.after(80, self._poll_events)
        self.after_idle(self._fit_to_screen)

    def _build_vars(self):
        defaults = {
            "port": "COM3", "baud": "115200", "simulation": True,
            "mode": "连续模式", "frequency": "10.0", "pulse_count": "1", "pulse_width": "10",
            "t1_us": "10", "t1_ns": "0", "t2_us": "0", "t2_ns": "0",
            "t3_us": "0", "t3_ns": "0",
        }
        self.v = {key: (tk.BooleanVar(value=value) if isinstance(value, bool) else tk.StringVar(value=value))
                  for key, value in defaults.items()}
        self.loads = [tk.BooleanVar(value=True) for _ in range(4)]
        self.status = tk.StringVar(value="未连接（可勾选仿真模式试用）")
        self.connection = tk.StringVar(value="OFFLINE")
        self.run_state = tk.StringVar(value="STANDBY")
        self.preview = tk.StringVar()
        for variable in list(self.v.values()) + self.loads:
            variable.trace_add("write", lambda *_: self._update_preview())

    def _configure_style(self):
        self.configure(bg=self.BG)
        style = ttk.Style(self); style.theme_use("clam")
        style.configure("TFrame", background=self.BG)
        style.configure("Panel.TFrame", background=self.PANEL)
        style.configure("TLabel", background=self.PANEL, foreground=self.TEXT, font=("Microsoft YaHei UI", 9))
        style.configure("Title.TLabel", background=self.BG, foreground="#f2f7fa", font=("Segoe UI", 17, "bold"))
        style.configure("Sub.TLabel", background=self.BG, foreground=self.MUTED, font=("Segoe UI", 8))
        style.configure("Readout.TLabel", background="#0e151d", foreground=self.GREEN, font=("Consolas", 11, "bold"), padding=8)
        style.configure("Channel.TLabel", background=self.PANEL, foreground=self.CYAN, font=("Segoe UI", 11, "bold"), anchor="center")
        style.configure("Hint.TLabel", background=self.PANEL, foreground=self.MUTED, font=("Microsoft YaHei UI", 8))
        style.configure("TLabelframe", background=self.PANEL, bordercolor="#263442", relief="solid", borderwidth=1)
        style.configure("TLabelframe.Label", background=self.PANEL, foreground="#a7b8c5", font=("Microsoft YaHei UI", 9, "bold"))
        style.configure("TEntry", fieldbackground=self.FIELD, foreground="#f3f7fa", insertcolor=self.CYAN, bordercolor="#334454", lightcolor="#334454", darkcolor="#334454", padding=5)
        style.configure("Fixed.TEntry", fieldbackground="#101821", foreground=self.GREEN,
                        bordercolor="#293846", padding=5)
        style.map("Fixed.TEntry",
                  fieldbackground=[("readonly", "#101821")],
                  foreground=[("readonly", self.GREEN)])
        style.configure("TCombobox", fieldbackground=self.FIELD, foreground="#f3f7fa", arrowcolor="#8ca5b6", bordercolor="#334454", padding=5)
        style.map("TCombobox", fieldbackground=[("readonly", self.FIELD)], foreground=[("readonly", "#f3f7fa")])
        style.configure("TCheckbutton", background=self.PANEL, foreground="#c3cdd2")
        style.map("TCheckbutton", background=[("active", self.PANEL)])
        style.configure("TRadiobutton", background=self.PANEL, foreground="#c3cdd2")
        style.map("TRadiobutton", background=[("active", self.PANEL)])
        style.configure("TButton", background="#202d39", foreground="#dfe8ee", borderwidth=0, padding=(9, 6), font=("Microsoft YaHei UI", 9))
        style.map("TButton", background=[("active", "#2b3c4a"), ("pressed", "#17222c")])
        style.configure("Accent.TButton", background="#3187b7", foreground="white", font=("Microsoft YaHei UI", 9, "bold"), padding=(9, 7))
        style.map("Accent.TButton", background=[("active", "#3c9aca"), ("pressed", "#276f98")])
        style.configure("Run.TButton", background="#238b6c", foreground="white", font=("Microsoft YaHei UI", 9, "bold"), padding=(9, 9))
        style.map("Run.TButton", background=[("active", "#2da681"), ("pressed", "#1c7057")])
        style.configure("Danger.TButton", background="#a84655", foreground="white", font=("Microsoft YaHei UI", 9, "bold"), padding=(9, 9))
        style.map("Danger.TButton", background=[("active", "#c25768"), ("pressed", "#873744")])
        self.option_add("*TCombobox*Listbox.background", self.FIELD)
        self.option_add("*TCombobox*Listbox.foreground", "#f0f6f8")

    def _build_redesigned_ui(self):
        """面向日常实验操作的四通道仪器控制台。"""
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=0)

        # 标题栏
        header = ttk.Frame(self, padding=(18, 12, 18, 9))
        header.grid(row=0, column=0, sticky="ew")
        title_box = ttk.Frame(header)
        title_box.pack(side="left")
        ttk.Label(title_box, text="LIBS TIMING CONTROL", style="Title.TLabel").pack(anchor="w")
        ttk.Label(title_box, text="4-CHANNEL DIGITAL DELAY GENERATOR", style="Sub.TLabel").pack(anchor="w", pady=(1, 0))
        state_box = tk.Frame(header, bg="#101c24", highlightbackground="#274354", highlightthickness=1)
        state_box.pack(side="right", pady=4)
        tk.Label(state_box, text="●", bg="#101c24", fg=self.GREEN,
                 font=("Segoe UI", 9, "bold")).pack(side="left", padx=(10, 4), pady=5)
        tk.Label(state_box, textvariable=self.connection, bg="#101c24", fg="#b9dbe6",
                 font=("Consolas", 9, "bold")).pack(side="left", padx=(0, 10), pady=5)

        # 设备工具栏
        toolbar = ttk.LabelFrame(self, text=" 设备 ", padding=(10, 7))
        toolbar.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 8))
        toolbar.columnconfigure(1, weight=1)
        ttk.Label(toolbar, text="串口").grid(row=0, column=0, padx=(0, 6))
        self.port_box = ttk.Combobox(toolbar, textvariable=self.v["port"], width=22)
        self.port_box.grid(row=0, column=1, sticky="ew")
        ttk.Button(toolbar, text="刷新", command=self._refresh_ports).grid(row=0, column=2, padx=(5, 14))
        ttk.Label(toolbar, text="波特率").grid(row=0, column=3, padx=(0, 6))
        ttk.Entry(toolbar, textvariable=self.v["baud"], width=10, justify="center").grid(row=0, column=4)
        ttk.Checkbutton(toolbar, text="仿真", variable=self.v["simulation"]).grid(row=0, column=5, padx=14)
        ttk.Button(toolbar, text="连接设备", command=self.connect, style="Accent.TButton").grid(row=0, column=6, padx=(0, 5))
        ttk.Button(toolbar, text="断开", command=self.disconnect).grid(row=0, column=7)

        workspace = ttk.Frame(self, padding=(12, 0, 12, 0))
        workspace.grid(row=2, column=0, sticky="nsew")
        workspace.columnconfigure(0, weight=1)
        workspace.columnconfigure(1, minsize=238)
        workspace.rowconfigure(0, weight=0)

        # 左侧：通道参数矩阵
        timing = ttk.LabelFrame(workspace, text=" 通道参数 ", padding=10)
        timing.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        timing.columnconfigure(0, weight=1)

        table_header = tk.Frame(timing, bg="#18232d")
        table_header.grid(row=0, column=0, sticky="ew", pady=(0, 5))
        column_specs = (("通道", 8), ("延时 / μs", 16), ("延时 / ns", 16),
                        ("脉宽 / μs", 13), ("频率 / Hz", 13), ("脉冲数", 12), ("50 Ω", 8))
        for col, (label, width) in enumerate(column_specs):
            table_header.columnconfigure(col, weight=1 if col else 0, minsize=width * 7)
            tk.Label(table_header, text=label, bg="#18232d", fg="#91a3b0",
                     font=("Microsoft YaHei UI", 8)).grid(row=0, column=col, sticky="ew", padx=4, pady=7)

        self.count_entries = []
        for channel in range(4):
            row_frame = tk.Frame(timing, bg="#111a23", highlightbackground="#263746", highlightthickness=1)
            row_frame.grid(row=channel + 1, column=0, sticky="ew", pady=3)
            for col, (_, width) in enumerate(column_specs):
                row_frame.columnconfigure(col, weight=1 if col else 0, minsize=width * 7)
            badge = tk.Label(row_frame, text=f"T{channel}", bg="#173747", fg="#70d0ef",
                             font=("Segoe UI", 10, "bold"), width=5)
            badge.grid(row=0, column=0, sticky="nsew", padx=(5, 4), pady=5)
            if channel == 0:
                us_entry = ttk.Entry(row_frame, justify="center", style="Fixed.TEntry", width=8)
                us_entry.insert(0, "0"); us_entry.configure(state="readonly")
                ns_entry = ttk.Entry(row_frame, justify="center", style="Fixed.TEntry", width=8)
                ns_entry.insert(0, "0"); ns_entry.configure(state="readonly")
            else:
                us_entry = ttk.Entry(row_frame, textvariable=self.v[f"t{channel}_us"], justify="center", width=8)
                ns_entry = ttk.Entry(row_frame, textvariable=self.v[f"t{channel}_ns"], justify="center", width=8)
            us_entry.grid(row=0, column=1, sticky="ew", padx=4, pady=5)
            ns_entry.grid(row=0, column=2, sticky="ew", padx=4, pady=5)
            ttk.Entry(row_frame, textvariable=self.v["pulse_width"], justify="center", width=8).grid(
                row=0, column=3, sticky="ew", padx=4, pady=5)
            ttk.Entry(row_frame, textvariable=self.v["frequency"], justify="center", width=8).grid(
                row=0, column=4, sticky="ew", padx=4, pady=5)
            count_entry = ttk.Entry(row_frame, textvariable=self.v["pulse_count"], justify="center", width=8)
            count_entry.grid(row=0, column=5, sticky="ew", padx=4, pady=5)
            self.count_entries.append(count_entry)
            ttk.Checkbutton(row_frame, text="", variable=self.loads[channel]).grid(
                row=0, column=6, padx=7, pady=5)
        self.count_entry = self.count_entries[0]

        info = tk.Frame(timing, bg="#101c25", highlightbackground="#243b49", highlightthickness=1)
        info.grid(row=5, column=0, sticky="ew", pady=(8, 0))
        tk.Label(info, text="同步参数", bg="#101c25", fg=self.CYAN,
                 font=("Microsoft YaHei UI", 8, "bold")).pack(side="left", padx=(9, 7), pady=6)
        tk.Label(info, text="四通道脉宽、频率和脉冲数联动；修改任意一行会同步全部通道。",
                 bg="#101c25", fg="#91a1ac", font=("Microsoft YaHei UI", 8)).pack(side="left", pady=6)
        tk.Label(info, text="T0 延时固定为零", bg="#101c25", fg="#91a1ac",
                 font=("Microsoft YaHei UI", 8)).pack(side="right", padx=9, pady=6)

        # 右侧：模式、运行和配置
        control = ttk.LabelFrame(workspace, text=" 运行控制 ", padding=10)
        control.grid(row=0, column=1, sticky="nsew")
        control.columnconfigure(0, weight=1)
        control.rowconfigure(0, weight=1)
        control_stack = ttk.Frame(control, style="Panel.TFrame")
        control_stack.grid(row=0, column=0, sticky="ew")
        mode_bar = tk.Frame(control_stack, bg=self.PANEL, highlightbackground="#263746", highlightthickness=1)
        mode_bar.pack(fill="x", pady=(0, 9))
        tk.Label(mode_bar, text="工作模式", bg="#1b2935", fg="#a8bac6",
                 font=("Microsoft YaHei UI", 9), width=8).pack(side="left", fill="y", padx=(0, 1))
        mode_box = ttk.Combobox(mode_bar, textvariable=self.v["mode"],
                                values=("连续模式", "脉冲模式", "外触发模式"),
                                state="readonly")
        mode_box.pack(side="left", fill="x", expand=True, padx=5, pady=5)
        mode_box.bind("<<ComboboxSelected>>", lambda _: self._update_mode())

        ttk.Button(control_stack, text="更新参数", command=self.apply_parameters,
                   style="Accent.TButton").pack(fill="x")
        action_bar = ttk.Frame(control_stack, style="Panel.TFrame")
        action_bar.pack(fill="x", pady=(7, 0))
        ttk.Button(action_bar, text="启动运行", command=self.run_controller,
                   style="Run.TButton").pack(fill="x")
        ttk.Button(action_bar, text="停止运行", command=self.stop_controller,
                   style="Danger.TButton").pack(fill="x", pady=(6, 0))

        ttk.Separator(control_stack, orient="horizontal").pack(fill="x", pady=10)
        config_bar = ttk.Frame(control_stack, style="Panel.TFrame")
        config_bar.pack(fill="x")
        ttk.Button(config_bar, text="保存配置", command=self.save_config).pack(fill="x")
        ttk.Button(config_bar, text="加载配置", command=self.load_config).pack(fill="x", pady=(6, 0))

        # 底部：通信信息合并为一张卡片
        comm = ttk.LabelFrame(self, text=" 通信监视 ", padding=(8, 6))
        comm.grid(row=3, column=0, sticky="ew", padx=12, pady=(8, 8))
        comm.columnconfigure(1, weight=1)
        ttk.Label(comm, text="发送帧预览", foreground=self.CYAN,
                  font=("Microsoft YaHei UI", 9, "bold")).grid(
                      row=0, column=0, sticky="nw", padx=(0, 10), pady=3)
        ttk.Label(comm, textvariable=self.preview, style="Hint.TLabel", font=("Consolas", 8), anchor="w").grid(row=0, column=1, sticky="ew", pady=3)
        ttk.Separator(comm, orient="horizontal").grid(row=1, column=0, columnspan=3, sticky="ew", pady=4)
        self.log = tk.Text(comm, bg="#0d141b", fg="#a9bac5", relief="flat",
                           font=("Consolas", 8), height=3, wrap="none")
        self.log.grid(row=2, column=0, columnspan=2, sticky="ew")
        scroll = ttk.Scrollbar(comm, orient="vertical", command=self.log.yview)
        scroll.grid(row=2, column=2, sticky="ns")
        self.log.configure(yscrollcommand=scroll.set, state="disabled")
        status_bar = tk.Frame(comm, bg="#101c24", highlightbackground="#263d4a", highlightthickness=1)
        status_bar.grid(row=3, column=0, columnspan=3, sticky="ew", pady=(6, 0))
        tk.Label(status_bar, text="设备状态", bg="#101c24", fg="#8195a2",
                 font=("Microsoft YaHei UI", 8)).pack(side="left", padx=(9, 6), pady=5)
        tk.Label(status_bar, textvariable=self.run_state, bg="#101c24", fg=self.GREEN,
                 font=("Consolas", 9, "bold")).pack(side="left", pady=5)
        tk.Label(status_bar, textvariable=self.status, bg="#101c24", fg="#95a6b1",
                 font=("Microsoft YaHei UI", 8)).pack(side="right", padx=9, pady=5)

    def _build_compact_ui(self):
        """紧凑布局：连接、参数、操作和日志按使用顺序集中排列。"""
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=0)

        header = ttk.Frame(self, padding=(12, 8, 12, 6))
        header.grid(row=0, column=0, sticky="ew")
        ttk.Label(header, text="LIBS TIMING CONTROL", style="Title.TLabel").pack(side="left")
        ttk.Label(header, text="四通道时序控制器", style="Sub.TLabel").pack(side="left", padx=(12, 0), pady=(5, 0))
        ttk.Label(header, textvariable=self.connection, style="Sub.TLabel").pack(side="right", pady=(5, 0))

        # 顶部连接条仅保留日常会用到的项目。
        conn = ttk.LabelFrame(self, text=" 设备连接 ", padding=(8, 5))
        conn.grid(row=1, column=0, sticky="ew", padx=8, pady=(0, 5))
        conn.columnconfigure(1, weight=1)
        ttk.Label(conn, text="串口").grid(row=0, column=0, padx=(0, 6))
        self.port_box = ttk.Combobox(conn, textvariable=self.v["port"], width=18)
        self.port_box.grid(row=0, column=1, sticky="ew")
        ttk.Button(conn, text="刷新", command=self._refresh_ports).grid(row=0, column=2, padx=(5, 9))
        ttk.Label(conn, text="波特率").grid(row=0, column=3, padx=(0, 6))
        ttk.Entry(conn, textvariable=self.v["baud"], width=10).grid(row=0, column=4)
        ttk.Checkbutton(conn, text="仿真模式", variable=self.v["simulation"]).grid(row=0, column=5, padx=8)
        ttk.Button(conn, text="连接", command=self.connect, style="Accent.TButton").grid(row=0, column=6, padx=(0, 5))
        ttk.Button(conn, text="断开", command=self.disconnect).grid(row=0, column=7)

        content = ttk.Frame(self, style="Panel.TFrame", padding=6)
        content.grid(row=2, column=0, sticky="nsew", padx=8)
        content.columnconfigure(0, weight=1)
        content.columnconfigure(1, minsize=185)
        content.rowconfigure(1, weight=0)

        parameters = ttk.LabelFrame(content, text=" 时序参数 ", padding=8)
        parameters.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        parameters.columnconfigure(0, minsize=48)
        parameters.columnconfigure((1, 2), weight=2, uniform="delay")
        parameters.columnconfigure((3, 4, 5), weight=1, uniform="pulse")
        parameters.columnconfigure(6, minsize=70)

        # 公共参数集中在同一行，避免在多个面板中来回查找。
        common_bar = ttk.Frame(parameters, style="Panel.TFrame")
        common_bar.grid(row=0, column=0, columnspan=4, sticky="ew", pady=(0, 5))
        common_bar.columnconfigure((1, 3, 5), weight=1, uniform="common_value")
        ttk.Label(common_bar, text="频率").grid(row=0, column=0, sticky="e")
        frequency_box = ttk.Frame(common_bar, style="Panel.TFrame")
        frequency_box.grid(row=0, column=1, sticky="ew", padx=(5, 14))
        frequency_box.columnconfigure(0, weight=1)
        ttk.Entry(frequency_box, textvariable=self.v["frequency"], width=7, justify="center").grid(row=0, column=0, sticky="ew")
        ttk.Label(frequency_box, text=" Hz", style="Hint.TLabel").grid(row=0, column=1)
        ttk.Label(common_bar, text="脉宽").grid(row=0, column=2, sticky="e")
        width_box = ttk.Frame(common_bar, style="Panel.TFrame")
        width_box.grid(row=0, column=3, sticky="ew", padx=(5, 14))
        width_box.columnconfigure(0, weight=1)
        ttk.Entry(width_box, textvariable=self.v["pulse_width"], width=7, justify="center").grid(row=0, column=0, sticky="ew")
        ttk.Label(width_box, text=" μs", style="Hint.TLabel").grid(row=0, column=1)
        ttk.Label(common_bar, text="脉冲数").grid(row=0, column=4, sticky="e")
        self.count_entry = ttk.Entry(common_bar, textvariable=self.v["pulse_count"], width=7, justify="center")
        self.count_entry.grid(row=0, column=5, sticky="ew", padx=(5, 0))
        # 公共值在下方四个通道行中重复显示，并共享同一变量。
        common_bar.grid_remove()

        headings = ("通道", "延时 (μs)", "延时 (ns)", "脉宽 (μs)",
                    "频率 (Hz)", "脉冲数", "50 Ω")
        for col, heading in enumerate(headings):
            ttk.Label(parameters, text=heading, style="Hint.TLabel", anchor="center").grid(
                row=1, column=col, sticky="ew", pady=(3, 2)
            )
        self.count_entries = []
        for row in range(4):
            ttk.Label(parameters, text=f"T{row}", style="Channel.TLabel").grid(
                row=row + 2, column=0, sticky="ew", pady=2
            )
            if row == 0:
                fixed_us = ttk.Entry(parameters, justify="center", style="Fixed.TEntry")
                fixed_us.insert(0, "0"); fixed_us.configure(state="readonly")
                fixed_us.grid(row=row + 2, column=1, sticky="ew", padx=3, pady=2)
                fixed_ns = ttk.Entry(parameters, justify="center", style="Fixed.TEntry")
                fixed_ns.insert(0, "0"); fixed_ns.configure(state="readonly")
                fixed_ns.grid(row=row + 2, column=2, sticky="ew", padx=3, pady=2)
            else:
                ttk.Entry(parameters, textvariable=self.v[f"t{row}_us"], justify="center").grid(
                    row=row + 2, column=1, sticky="ew", padx=3, pady=2
                )
                ttk.Entry(parameters, textvariable=self.v[f"t{row}_ns"], justify="center").grid(
                    row=row + 2, column=2, sticky="ew", padx=3, pady=2
                )
            ttk.Entry(parameters, textvariable=self.v["pulse_width"], width=7,
                      justify="center").grid(
                row=row + 2, column=3, sticky="ew", padx=3, pady=2
            )
            ttk.Entry(parameters, textvariable=self.v["frequency"], width=7,
                      justify="center").grid(
                row=row + 2, column=4, sticky="ew", padx=3, pady=2
            )
            count_entry = ttk.Entry(parameters, textvariable=self.v["pulse_count"], width=7,
                                    justify="center")
            count_entry.grid(row=row + 2, column=5, sticky="ew", padx=3, pady=2)
            self.count_entries.append(count_entry)
            ttk.Checkbutton(parameters, text="", variable=self.loads[row]).grid(
                row=row + 2, column=6, sticky="", padx=(9, 0), pady=2
            )
        self.count_entry = self.count_entries[0]
        ttk.Label(parameters,
                  text="T0 为时基零点；T1–T3 纳秒延时必须为 5 ns 的整数倍。",
                  style="Hint.TLabel").grid(row=6, column=0, columnspan=7, sticky="w", pady=(5, 0))

        control = ttk.LabelFrame(content, text=" 运行控制 ", padding=7)
        control.grid(row=0, column=1, sticky="nsew")
        ttk.Label(control, text="工作模式", style="Hint.TLabel").pack(anchor="w", pady=(0, 2))
        mode_box = ttk.Combobox(control, textvariable=self.v["mode"],
                                values=("连续模式", "脉冲模式", "外触发模式"),
                                state="readonly", width=13)
        mode_box.pack(fill="x", pady=(0, 6))
        mode_box.bind("<<ComboboxSelected>>", lambda _: self._update_mode())
        ttk.Button(control, text="更新参数", command=self.apply_parameters,
                   style="Accent.TButton").pack(fill="x")
        ttk.Button(control, text="运行控制器", command=self.run_controller,
                   style="Run.TButton").pack(fill="x", pady=(5, 0))
        ttk.Button(control, text="停止控制器", command=self.stop_controller,
                   style="Danger.TButton").pack(fill="x", pady=(5, 0))
        ttk.Label(control, text="当前状态", style="Hint.TLabel").pack(anchor="w", pady=(8, 2))
        ttk.Label(control, textvariable=self.run_state, style="Readout.TLabel", anchor="center").pack(fill="x")
        config_row = ttk.Frame(control, style="Panel.TFrame")
        config_row.pack(fill="x", pady=(6, 0))
        ttk.Button(config_row, text="保存", command=self.save_config).pack(side="left", fill="x", expand=True, padx=(0, 3))
        ttk.Button(config_row, text="加载", command=self.load_config).pack(side="left", fill="x", expand=True, padx=(3, 0))

        preview = ttk.LabelFrame(self, text=" 发送帧预览 ", padding=(8, 5))
        preview.grid(row=3, column=0, sticky="ew", padx=8, pady=(5, 0))
        ttk.Label(preview, textvariable=self.preview, style="Hint.TLabel",
                  font=("Consolas", 8), anchor="w").pack(fill="x")

        # 通讯日志为辅助区域，固定为五行高度。
        log_frame = ttk.LabelFrame(self, text=" 通讯记录 ", padding=(8, 5))
        log_frame.grid(row=4, column=0, sticky="ew", padx=8, pady=(5, 0))
        log_frame.columnconfigure(0, weight=1)
        self.log = tk.Text(log_frame, bg="#030303", fg="#b7c8d0", relief="flat",
                           font=("Consolas", 8), height=3, wrap="none")
        self.log.grid(row=0, column=0, sticky="ew")
        scroll = ttk.Scrollbar(log_frame, orient="vertical", command=self.log.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.log.configure(yscrollcommand=scroll.set, state="disabled")
        ttk.Button(log_frame, text="清空", command=self._clear_log).grid(row=0, column=2, padx=(6, 0), sticky="ns")

        status = ttk.Frame(self, style="Panel.TFrame", padding=(10, 6))
        status.grid(row=5, column=0, sticky="ew", padx=8, pady=(4, 6))
        ttk.Label(status, text="● SYSTEM", foreground=self.CYAN).pack(side="left")
        ttk.Label(status, textvariable=self.status).pack(side="left", padx=(10, 0), fill="x", expand=True)

    def _build_ui(self):
        self.columnconfigure(1, weight=1); self.rowconfigure(1, weight=1)
        header = ttk.Frame(self, padding=(18, 13, 18, 10)); header.grid(row=0, column=0, columnspan=2, sticky="ew")
        ttk.Label(header, text="LIBS TIMING CONTROL", style="Title.TLabel").pack(side="left")
        ttk.Label(header, text="四通道数字延时与脉冲控制", style="Sub.TLabel").pack(side="left", padx=(16, 0), pady=(8, 0))
        ttk.Label(header, textvariable=self.connection, style="Sub.TLabel").pack(side="right", pady=(8, 0))

        left = ttk.Frame(self, style="Panel.TFrame", padding=12); left.grid(row=1, column=0, sticky="nsew", padx=(12, 6))
        main = ttk.Frame(self, style="Panel.TFrame", padding=12); main.grid(row=1, column=1, sticky="nsew", padx=(6, 12))
        main.columnconfigure(0, weight=1); main.rowconfigure(2, weight=1)

        conn = ttk.LabelFrame(left, text=" 设备连接 ", padding=9); conn.pack(fill="x", pady=(0, 10)); conn.columnconfigure(1, weight=1)
        ttk.Label(conn, text="串口").grid(row=0, column=0, sticky="w", padx=(0, 8), pady=3)
        self.port_box = ttk.Combobox(conn, textvariable=self.v["port"], width=16)
        self.port_box.grid(row=0, column=1, sticky="ew", pady=3)
        ttk.Button(conn, text="刷新", command=self._refresh_ports).grid(row=0, column=2, padx=(5, 0))
        self._entry(conn, "波特率", "baud", 1)
        ttk.Checkbutton(conn, text="仿真模式（不发送到硬件）", variable=self.v["simulation"]).grid(row=2, column=0, columnspan=3, sticky="w", pady=(5, 3))
        ttk.Button(conn, text="连接", command=self.connect, style="Accent.TButton").grid(row=3, column=0, columnspan=2, sticky="ew", padx=(0, 3), pady=(6, 0))
        ttk.Button(conn, text="断开", command=self.disconnect).grid(row=3, column=2, sticky="ew", padx=(3, 0), pady=(6, 0))

        mode = ttk.LabelFrame(left, text=" 工作模式 ", padding=9); mode.pack(fill="x", pady=(0, 10))
        for row, label in enumerate(("连续模式", "脉冲模式", "外触发模式")):
            ttk.Radiobutton(mode, text=label, variable=self.v["mode"], value=label, command=self._update_mode).grid(row=row, column=0, sticky="w", pady=3)
        mode.columnconfigure(1, weight=1)
        ttk.Label(mode, text="脉冲数").grid(row=0, column=1, sticky="w", padx=(15, 5))
        self.count_entry = ttk.Entry(mode, textvariable=self.v["pulse_count"], width=9)
        self.count_entry.grid(row=1, column=1, sticky="ew", padx=(15, 0))
        ttk.Label(mode, text="0–65535；仅脉冲模式有效", style="Hint.TLabel").grid(row=2, column=1, sticky="w", padx=(15, 0))

        common = ttk.LabelFrame(left, text=" 公共脉冲参数 ", padding=9); common.pack(fill="x", pady=(0, 10)); common.columnconfigure(1, weight=1)
        self._entry(common, "频率 (Hz)", "frequency", 0)
        self._entry(common, "脉宽 (μs)", "pulse_width", 1)
        ttk.Label(common, text="频率 0.1–20.0 Hz，分辨率 0.1 Hz\n脉宽 ≥ 2 μs，分辨率 1 μs", style="Hint.TLabel").grid(row=2, column=0, columnspan=2, sticky="w", pady=(5, 0))

        actions = ttk.LabelFrame(left, text=" 控制 ", padding=9); actions.pack(fill="x")
        ttk.Button(actions, text="更新参数", command=self.apply_parameters, style="Accent.TButton").pack(fill="x")
        ttk.Button(actions, text="运行控制器", command=self.run_controller, style="Run.TButton").pack(fill="x", pady=(6, 0))
        ttk.Button(actions, text="停止控制器", command=self.stop_controller, style="Danger.TButton").pack(fill="x", pady=(6, 0))
        ttk.Button(actions, text="保存配置", command=self.save_config).pack(fill="x", pady=(6, 0))
        ttk.Button(actions, text="加载配置", command=self.load_config).pack(fill="x", pady=(6, 0))

        channels = ttk.LabelFrame(main, text=" 四通道时序参数 ", padding=12); channels.grid(row=0, column=0, sticky="ew")
        channels.columnconfigure((1, 2, 3), weight=1)
        headings = ("通道", "延时 (μs)", "延时 (ns)", "输出阻抗")
        for col, heading in enumerate(headings): ttk.Label(channels, text=heading, style="Hint.TLabel", anchor="center").grid(row=0, column=col, sticky="ew", pady=(0, 6))
        for row in range(4):
            ttk.Label(channels, text=f"T{row}", style="Channel.TLabel").grid(row=row+1, column=0, sticky="ew", padx=(0, 10), pady=4)
            if row == 0:
                ttk.Label(channels, text="0", style="Readout.TLabel", anchor="center").grid(row=row+1, column=1, sticky="ew", padx=4)
                ttk.Label(channels, text="0", style="Readout.TLabel", anchor="center").grid(row=row+1, column=2, sticky="ew", padx=4)
            else:
                ttk.Entry(channels, textvariable=self.v[f"t{row}_us"], justify="center").grid(row=row+1, column=1, sticky="ew", padx=4)
                ttk.Entry(channels, textvariable=self.v[f"t{row}_ns"], justify="center").grid(row=row+1, column=2, sticky="ew", padx=4)
            ttk.Checkbutton(channels, text="50 Ω", variable=self.loads[row]).grid(row=row+1, column=3, padx=(20, 0), pady=4)
        ttk.Label(channels, text="T0 为固定时基；T1–T3 的 ns 分辨率为 5 ns。取消 50 Ω 即选择高阻。", style="Hint.TLabel").grid(row=5, column=0, columnspan=4, sticky="w", pady=(10, 0))

        telemetry = ttk.Frame(main, style="Panel.TFrame", padding=(0, 12)); telemetry.grid(row=1, column=0, sticky="ew"); telemetry.columnconfigure(1, weight=1)
        ttk.Label(telemetry, text="SYSTEM STATE", style="Hint.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(telemetry, textvariable=self.run_state, style="Readout.TLabel").grid(row=1, column=0, sticky="ew", padx=(0, 8))
        ttk.Label(telemetry, text="TX FRAME PREVIEW", style="Hint.TLabel").grid(row=0, column=1, sticky="w")
        ttk.Label(telemetry, textvariable=self.preview, style="Readout.TLabel", anchor="w", wraplength=750).grid(row=1, column=1, sticky="ew")

        log_frame = ttk.LabelFrame(main, text=" 通信日志 ", padding=8); log_frame.grid(row=2, column=0, sticky="nsew"); log_frame.columnconfigure(0, weight=1); log_frame.rowconfigure(0, weight=1)
        self.log = tk.Text(log_frame, bg="#030303", fg="#b7c8d0", insertbackground=self.CYAN, relief="flat", font=("Consolas", 9), height=12, wrap="none")
        self.log.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(log_frame, orient="vertical", command=self.log.yview); scroll.grid(row=0, column=1, sticky="ns"); self.log.configure(yscrollcommand=scroll.set, state="disabled")
        ttk.Button(log_frame, text="清空日志", command=self._clear_log).grid(row=1, column=1, sticky="e", pady=(6, 0))

        status = ttk.Frame(self, style="Panel.TFrame", padding=(12, 8)); status.grid(row=2, column=0, columnspan=2, sticky="ew", padx=12, pady=(8, 10))
        ttk.Label(status, text="● SYSTEM", foreground=self.CYAN).pack(side="left")
        ttk.Label(status, textvariable=self.status).pack(side="left", padx=(12, 0), fill="x", expand=True)

    def _entry(self, parent, label, key, row):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=(0, 8), pady=3)
        entry = ttk.Entry(parent, textvariable=self.v[key], width=16); entry.grid(row=row, column=1, columnspan=2, sticky="ew", pady=3)
        return entry

    def _fit_to_screen(self):
        self.update_idletasks(); sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        w, h = max(980, min(1080, sw-40)), max(620, min(660, sh-80))
        self.geometry(f"{w}x{h}+{max(0,(sw-w)//2)}+{max(0,(sh-h)//2-15)}")

    def _refresh_ports(self):
        ports = available_ports(); values = [f"{device} — {desc}" if desc else device for device, desc in ports]
        self.port_box["values"] = values
        if ports and (not self.v["port"].get() or self.v["port"].get().split(" — ")[0] not in [p[0] for p in ports]):
            self.v["port"].set(values[0])
        self.status.set(f"检测到 {len(ports)} 个串口" if ports else "未检测到串口；可使用仿真模式")

    def _parameters(self) -> TimingParameters:
        modes = {"连续模式": Mode.CONTINUOUS, "脉冲模式": Mode.FINITE, "外触发模式": Mode.EXTERNAL_TRIGGER}
        return TimingParameters(
            mode=modes[self.v["mode"].get()], pulse_count=int(self.v["pulse_count"].get()),
            frequency_hz=float(self.v["frequency"].get()), pulse_width_us=int(self.v["pulse_width"].get()),
            delays_us=tuple(int(self.v[f"t{i}_us"].get()) for i in range(1, 4)),
            delays_ns=tuple(int(self.v[f"t{i}_ns"].get()) for i in range(1, 4)),
            loads_50ohm=tuple(value.get() for value in self.loads),
        )

    def _update_preview(self):
        if not hasattr(self, "preview"): return
        try: self.preview.set(frame_hex(build_parameter_frame(self._parameters())))
        except Exception as exc: self.preview.set(f"参数待修正：{exc}")

    def _update_mode(self):
        finite = self.v["mode"].get() == "脉冲模式"
        entries = getattr(self, "count_entries", [self.count_entry])
        for entry in entries:
            entry.configure(state="normal" if finite else "disabled")
        if not finite and not self.v["pulse_count"].get(): self.v["pulse_count"].set("0")

    def connect(self):
        try:
            port = self.v["port"].get().split(" — ")[0].strip()
            self.controller.open(port, int(self.v["baud"].get()), self.v["simulation"].get())
            label = "SIMULATION" if self.controller.simulation else f"ONLINE · {port}"
            self.connection.set(label); self.status.set("控制器连接成功"); self.run_state.set("READY")
        except Exception as exc: messagebox.showerror("连接失败", str(exc)); self.status.set(str(exc))

    def disconnect(self):
        self._cancel_completion_timer()
        self.controller.close(); self.connection.set("OFFLINE"); self.run_state.set("STANDBY"); self.status.set("已断开连接")

    def apply_parameters(self): self._send("参数已更新", self.controller.apply)

    def run_controller(self):
        try:
            parameters = self._parameters()
            self.controller.run(parameters)
            self._cancel_completion_timer()
            self.run_state.set("RUNNING")
            if parameters.mode == Mode.FINITE:
                duration_ms = max(1, round(parameters.pulse_count / parameters.frequency_hz * 1000))
                self.status.set(
                    f"脉冲输出中：{parameters.pulse_count} 个，预计 {duration_ms / 1000:.2f} 秒后自动停止"
                )
                self._completion_job = self.after(duration_ms, self._finite_run_completed)
            elif parameters.mode == Mode.EXTERNAL_TRIGGER:
                self.status.set("外触发等待/运行中；收到外部上升沿后输出，点击停止结束")
            else:
                self.status.set("控制器连续运行中；点击停止结束")
        except (ValueError, TimingControllerError) as exc:
            self.status.set(str(exc)); messagebox.showerror("操作失败", str(exc))

    def _finite_run_completed(self):
        self._completion_job = None
        self.run_state.set("STOPPED")
        self.status.set("脉冲输出完成，控制器已按设定脉冲数自动停止")

    def _cancel_completion_timer(self):
        if self._completion_job is not None:
            try:
                self.after_cancel(self._completion_job)
            except tk.TclError:
                pass
            self._completion_job = None

    def _send(self, success, action, state=None):
        try:
            action(self._parameters()); self.status.set(success)
            if state: self.run_state.set(state)
        except (ValueError, TimingControllerError) as exc:
            self.status.set(str(exc)); messagebox.showerror("操作失败", str(exc))

    def stop_controller(self):
        self._cancel_completion_timer()
        try: self.controller.stop(); self.run_state.set("STOPPED"); self.status.set("已发送停止命令 DD DD")
        except TimingControllerError as exc: self.status.set(str(exc)); messagebox.showerror("停止失败", str(exc))

    def _config_dict(self):
        return {"version": 1, "mode": self.v["mode"].get(), "frequency_hz": self.v["frequency"].get(),
                "pulse_count": self.v["pulse_count"].get(), "pulse_width_us": self.v["pulse_width"].get(),
                "delays_us": [self.v[f"t{i}_us"].get() for i in range(1, 4)],
                "delays_ns": [self.v[f"t{i}_ns"].get() for i in range(1, 4)],
                "loads_50ohm": [value.get() for value in self.loads]}

    def save_config(self):
        try: self._parameters().validate()
        except Exception as exc: messagebox.showerror("无法保存", str(exc)); return
        path = filedialog.asksaveasfilename(defaultextension=".json", filetypes=[("JSON 配置", "*.json")], initialfile="timing_config.json")
        if path:
            Path(path).write_text(json.dumps(self._config_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
            self.status.set(f"配置已保存：{path}")

    def load_config(self):
        path = filedialog.askopenfilename(filetypes=[("JSON 配置", "*.json"), ("所有文件", "*.*")])
        if not path: return
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            loaded_mode = data["mode"]
            if loaded_mode.startswith("工作模式—"):
                loaded_mode = loaded_mode.removeprefix("工作模式—")
            self.v["mode"].set(loaded_mode); self.v["frequency"].set(str(data["frequency_hz"])); self.v["pulse_count"].set(str(data["pulse_count"])); self.v["pulse_width"].set(str(data["pulse_width_us"]))
            for i in range(3): self.v[f"t{i+1}_us"].set(str(data["delays_us"][i])); self.v[f"t{i+1}_ns"].set(str(data["delays_ns"][i])); self.loads[i].set(bool(data["loads_50ohm"][i]))
            self.loads[3].set(bool(data["loads_50ohm"][3])); self._parameters().validate(); self._update_mode(); self.status.set(f"配置已加载：{path}")
        except Exception as exc: messagebox.showerror("加载失败", f"配置文件无效：{exc}")

    def _enqueue_log(self, entry: LogEntry): self.events.put(("log", entry))

    def _poll_events(self):
        try:
            while True:
                kind, entry = self.events.get_nowait()
                if kind == "log":
                    self.log.configure(state="normal"); self.log.insert("end", entry.text + "\n"); self.log.see("end"); self.log.configure(state="disabled")
        except queue.Empty: pass
        self.after(80, self._poll_events)

    def _clear_log(self): self.log.configure(state="normal"); self.log.delete("1.0", "end"); self.log.configure(state="disabled")

    def _on_close(self):
        self._cancel_completion_timer()
        if self.controller.connected:
            try: self.controller.stop()
            except Exception: pass
        self.controller.close(); self.destroy()


def run():
    TimingApp().mainloop()
