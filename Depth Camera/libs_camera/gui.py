from __future__ import annotations

import json
import math
import queue
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Any

try:
    from PIL import Image, ImageDraw, ImageTk
except ImportError:
    Image = ImageDraw = ImageTk = None  # type: ignore[assignment]

try:
    import numpy as np
except ImportError:
    np = None  # type: ignore[assignment]

from .controller import CameraError, CameraSettings, DepthCameraController, DeviceInfo, FramePacket


BG = "#000000"
PANEL = "#080808"
CYAN = "#40dcff"
GREEN = "#00f0b5"
TEXT = "#d8e1e6"
MUTED = "#77878f"


def application_directory() -> Path:
    """返回源码运行目录或打包后 EXE 所在目录。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


class DepthCameraApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("LIBS Vision Control · 深度相机上位机")
        self.geometry("1480x700")
        self.minsize(1120, 700)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.controller: DepthCameraController | None = None
        self.devices: list[DeviceInfo] = []
        self.events: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.pending_frame: FramePacket | None = None
        self.current_frame: FramePacket | None = None
        self.photos: dict[str, Any] = {}
        self.display_geometry: dict[str, tuple[int, int, int, int, int, int]] = {}
        self.crosshair: tuple[int, int] | None = None
        self.view3d_yaw = math.radians(-35.0)
        self.view3d_pitch = math.radians(20.0)
        self.view3d_zoom = 1.0
        self.view3d_drag: tuple[int, int, float, float] | None = None
        self.view3d_last_render = 0.0
        self.busy = False
        self._build_vars()
        self._configure_styles()
        self._build_ui()
        self.after_idle(self._fit_to_screen)
        self.after(40, self._poll)
        self.after(150, self.refresh_devices)

    def _build_vars(self) -> None:
        values: dict[str, Any] = {
            "simulation": True,
            "device": "",
            "source_file": "",
            "resolution": "640 × 480",
            "fps": "30",
            "color": True,
            "depth": True,
            "infrared": False,
            "align": True,
            "spatial": True,
            "temporal": True,
            "hole": False,
            "max_distance": "4.0",
            "auto_exposure": True,
            "exposure": "8500",
            "gain": "16",
            "emitter": True,
            "laser": "150",
            "record": False,
            "record_file": "",
            "output_dir": str(application_directory() / "captures"),
        }
        self.v: dict[str, tk.Variable] = {}
        for key, value in values.items():
            self.v[key] = tk.BooleanVar(value=value) if isinstance(value, bool) else tk.StringVar(value=value)
        self.status = tk.StringVar(value="正在检查运行环境…")
        self.device_info = tk.StringVar(value="设备：未连接")
        self.frame_info = tk.StringVar(value="FRAME ----  |  0.0 FPS  |  -- ms")
        self.measurement = tk.StringVar(value="在深度画面中单击，以读取距离和三维坐标")
        self.record_state = tk.StringVar(value="未录像")

    def _configure_styles(self) -> None:
        self.configure(bg=BG)
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TFrame", background=BG)
        style.configure("Panel.TFrame", background=PANEL)
        style.configure("TLabel", background=PANEL, foreground=TEXT, font=("Microsoft YaHei UI", 9))
        style.configure("Panel.TLabel", background=PANEL, foreground=TEXT)
        style.configure("Hint.TLabel", background=PANEL, foreground=MUTED, font=("Microsoft YaHei UI", 8))
        style.configure("Title.TLabel", background=BG, foreground=CYAN, font=("Microsoft YaHei UI", 18, "bold"))
        style.configure("SubTitle.TLabel", background=BG, foreground="#71818a", font=("Microsoft YaHei UI", 9))
        style.configure("Status.TLabel", background="#050505", foreground="#8de8ff", padding=8)
        style.configure("Telemetry.TLabel", background=PANEL, foreground=GREEN, font=("Consolas", 10, "bold"))
        style.configure("TLabelframe", background=PANEL, bordercolor="#252525", relief="solid")
        style.configure("TLabelframe.Label", background=PANEL, foreground=CYAN, font=("Microsoft YaHei UI", 10, "bold"))
        style.configure("TEntry", fieldbackground="#111111", foreground="#f0f6f8", bordercolor="#303030", insertcolor=CYAN, padding=5)
        style.configure("TCombobox", fieldbackground="#111111", foreground="#f0f6f8", arrowcolor=CYAN, padding=4)
        style.map("TCombobox", fieldbackground=[("readonly", "#111111")], foreground=[("readonly", "#f0f6f8")])
        style.configure("TCheckbutton", background=PANEL, foreground="#c3cdd2")
        style.map("TCheckbutton", background=[("active", PANEL)], foreground=[("disabled", "#50585c")])
        style.configure("TButton", background="#202020", foreground="#e8eef1", borderwidth=0, padding=(8, 5), font=("Microsoft YaHei UI", 9))
        style.map("TButton", background=[("active", "#353535"), ("pressed", "#141414"), ("disabled", "#151515")])
        style.configure("Accent.TButton", background="#00a8d6", foreground="#03121b", font=("Microsoft YaHei UI", 9, "bold"))
        style.map("Accent.TButton", background=[("active", "#38d5ff"), ("pressed", "#0086aa")])
        style.configure("Danger.TButton", background="#b93b55", foreground="white")
        style.map("Danger.TButton", background=[("active", "#e34f6b"), ("pressed", "#8f2940")])
        style.configure("TNotebook", background=PANEL, borderwidth=0)
        style.configure("TNotebook.Tab", background="#121212", foreground="#89989f", padding=(13, 6))
        style.map("TNotebook.Tab", background=[("selected", "#073642")], foreground=[("selected", CYAN)])
        self.option_add("*TCombobox*Listbox.background", "#111111")
        self.option_add("*TCombobox*Listbox.foreground", "#f0f6f8")
        self.option_add("*TCombobox*Listbox.selectBackground", "#007f9f")

    def _build_ui(self) -> None:
        self.columnconfigure(0, minsize=292)
        self.columnconfigure(1, weight=1, minsize=560)
        self.columnconfigure(2, minsize=292)
        self.rowconfigure(1, weight=1)
        header = ttk.Frame(self, padding=(18, 13, 18, 10))
        header.grid(row=0, column=0, columnspan=3, sticky="ew")
        ttk.Label(header, text="LIBS VISION CONTROL", style="Title.TLabel").pack(side="left")
        ttk.Label(header, text="RealSense 深度相机 · 采集与测量系统", style="SubTitle.TLabel").pack(side="left", padx=(16, 0), pady=(8, 0))
        ttk.Label(header, textvariable=self.device_info, style="SubTitle.TLabel").pack(side="right", pady=(8, 0))

        left = ttk.Frame(self, style="Panel.TFrame", padding=10)
        left.grid(row=1, column=0, sticky="nsew", padx=(12, 6))
        center = ttk.Frame(self, style="Panel.TFrame", padding=10)
        center.grid(row=1, column=1, sticky="nsew", padx=6)
        right = ttk.Frame(self, style="Panel.TFrame", padding=10)
        right.grid(row=1, column=2, sticky="nsew", padx=(6, 12))
        center.columnconfigure(0, weight=1)
        center.rowconfigure(1, weight=1)

        ttk.Label(left, text="CAMERA CONTROL", style="Panel.TLabel", font=("Segoe UI", 10, "bold")).pack(anchor="w", padx=4, pady=(0, 6))
        ttk.Label(right, text="PROCESSING & OUTPUT", style="Panel.TLabel", font=("Segoe UI", 10, "bold")).pack(anchor="w", padx=4, pady=(0, 6))

        conn = ttk.LabelFrame(left, text=" 设备连接 ", padding=8)
        conn.pack(fill="x", pady=(0, 8))
        conn.columnconfigure(0, weight=1)
        device_row = ttk.Frame(conn, style="Panel.TFrame")
        device_row.grid(row=0, column=0, sticky="ew")
        device_row.columnconfigure(0, weight=1)
        self.device_combo = ttk.Combobox(device_row, textvariable=self.v["device"], state="readonly", width=25)
        self.device_combo.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        ttk.Button(device_row, text="刷新", command=self.refresh_devices).grid(row=0, column=1)
        ttk.Checkbutton(conn, text="仿真模式（不连接硬件）", variable=self.v["simulation"], command=self.refresh_devices).grid(row=1, column=0, sticky="w", pady=(6, 2))
        source = ttk.Frame(conn, style="Panel.TFrame")
        source.grid(row=2, column=0, sticky="ew", pady=(3, 0))
        source.columnconfigure(0, weight=1)
        ttk.Entry(source, textvariable=self.v["source_file"]).grid(row=0, column=0, sticky="ew", padx=(0, 4))
        ttk.Button(source, text="回放文件", command=self.choose_source).grid(row=0, column=1)
        ttk.Label(conn, text="回放留空即使用 USB 实时设备", style="Hint.TLabel").grid(row=3, column=0, sticky="w", pady=(3, 5))
        buttons = ttk.Frame(conn, style="Panel.TFrame")
        buttons.grid(row=4, column=0, sticky="ew")
        buttons.columnconfigure((0, 1), weight=1)
        self.start_button = ttk.Button(buttons, text="启动采集", command=self.start_camera, style="Accent.TButton")
        self.start_button.grid(row=0, column=0, sticky="ew", padx=(0, 3))
        self.stop_button = ttk.Button(buttons, text="停止采集", command=self.stop_camera, style="Danger.TButton", state="disabled")
        self.stop_button.grid(row=0, column=1, sticky="ew", padx=(3, 0))

        streams = ttk.LabelFrame(left, text=" 数据流配置 ", padding=8)
        streams.pack(fill="x", pady=(0, 8))
        streams.columnconfigure(1, weight=1)
        self._combo_row(streams, "分辨率", "resolution", ("424 × 240", "640 × 480", "848 × 480", "1280 × 720"), 0)
        self._combo_row(streams, "帧率", "fps", ("6", "15", "30", "60", "90"), 1)
        checks = ttk.Frame(streams, style="Panel.TFrame")
        checks.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(7, 2))
        ttk.Checkbutton(checks, text="彩色", variable=self.v["color"]).pack(side="left")
        ttk.Checkbutton(checks, text="深度", variable=self.v["depth"]).pack(side="left", padx=(10, 0))
        ttk.Checkbutton(checks, text="红外", variable=self.v["infrared"]).pack(side="left", padx=(10, 0))
        ttk.Checkbutton(streams, text="深度对齐到彩色画面", variable=self.v["align"]).grid(row=3, column=0, columnspan=2, sticky="w")
        self._entry_row(streams, "显示距离上限 (m)", "max_distance", 4)

        recording = ttk.LabelFrame(left, text=" 原始数据录像 ", padding=6)
        recording.pack(fill="both", expand=True)
        ttk.Checkbutton(recording, text="启动采集时同步录像", variable=self.v["record"]).pack(anchor="w")
        record_row = ttk.Frame(recording, style="Panel.TFrame")
        record_row.pack(fill="x", pady=(4, 1))
        record_row.columnconfigure(0, weight=1)
        ttk.Entry(record_row, textvariable=self.v["record_file"]).grid(row=0, column=0, sticky="ew", padx=(0, 4))
        ttk.Button(record_row, text="选择", command=self.choose_record_file).grid(row=0, column=1)
        ttk.Label(recording, textvariable=self.record_state, style="Hint.TLabel").pack(anchor="w")

        ttk.Label(center, text="REALTIME DEPTH VISION", style="Panel.TLabel", font=("Segoe UI", 10, "bold")).grid(row=0, column=0, sticky="w", padx=4, pady=(0, 6))
        self.notebook = ttk.Notebook(center)
        self.notebook.grid(row=1, column=0, sticky="nsew")
        dual = ttk.Frame(self.notebook, style="Panel.TFrame", padding=4)
        dual.columnconfigure((0, 1), weight=1, uniform="preview")
        dual.rowconfigure(1, weight=1)
        self.notebook.add(dual, text="彩色 / 深度")
        ttk.Label(dual, text="COLOR / RGB · 单击定位", style="Hint.TLabel").grid(row=0, column=0, sticky="w", padx=4)
        ttk.Label(dual, text="DEPTH / 单击测距", style="Hint.TLabel").grid(row=0, column=1, sticky="w", padx=4)
        self.color_canvas = self._preview_canvas(dual)
        self.color_canvas.grid(row=1, column=0, sticky="nsew", padx=(0, 3))
        self.depth_canvas = self._preview_canvas(dual)
        self.depth_canvas.grid(row=1, column=1, sticky="nsew", padx=(3, 0))
        self.color_canvas.bind("<Button-1>", lambda event: self._measure_click(event, "color"))
        self.depth_canvas.bind("<Button-1>", lambda event: self._measure_click(event, "depth"))
        infrared_tab = ttk.Frame(self.notebook, style="Panel.TFrame", padding=4)
        infrared_tab.columnconfigure(0, weight=1)
        infrared_tab.rowconfigure(1, weight=1)
        self.notebook.add(infrared_tab, text="红外")
        ttk.Label(infrared_tab, text="INFRARED STREAM", style="Hint.TLabel").grid(row=0, column=0, sticky="w", padx=4)
        self.infrared_canvas = self._preview_canvas(infrared_tab)
        self.infrared_canvas.grid(row=1, column=0, sticky="nsew")

        self.depth3d_tab = ttk.Frame(self.notebook, style="Panel.TFrame", padding=4)
        self.depth3d_tab.columnconfigure(0, weight=1)
        self.depth3d_tab.rowconfigure(1, weight=1)
        self.notebook.add(self.depth3d_tab, text="深度三维")
        ttk.Label(
            self.depth3d_tab,
            text="鼠标左键拖动旋转 · 滚轮缩放 · 双击复位 · 坐标轴：X 红 / Y 绿 / Z 青",
            style="Hint.TLabel",
        ).grid(row=0, column=0, sticky="w", padx=4)
        self.depth3d_canvas = tk.Canvas(
            self.depth3d_tab,
            bg=BG,
            highlightthickness=1,
            highlightbackground="#303030",
            cursor="fleur",
        )
        self.depth3d_canvas.grid(row=1, column=0, sticky="nsew")
        self.depth3d_canvas.bind("<Configure>", self._on_3d_configure)
        self.depth3d_canvas.bind("<ButtonPress-1>", self._start_3d_rotate)
        self.depth3d_canvas.bind("<B1-Motion>", self._drag_3d_rotate)
        self.depth3d_canvas.bind("<MouseWheel>", self._zoom_3d)
        self.depth3d_canvas.bind("<Double-Button-1>", self._reset_3d_view)
        self.notebook.bind("<<NotebookTabChanged>>", self._on_notebook_changed)

        log_tab = ttk.Frame(self.notebook, style="Panel.TFrame", padding=4)
        log_tab.columnconfigure(0, weight=1)
        log_tab.rowconfigure(0, weight=1)
        self.notebook.add(log_tab, text="运行日志")
        self.log_text = tk.Text(log_tab, bg="#050505", fg="#b7c7cf", insertbackground=CYAN, relief="flat", font=("Consolas", 9), state="disabled", wrap="word")
        self.log_text.grid(row=0, column=0, sticky="nsew")
        telemetry = ttk.Frame(center, style="Panel.TFrame", padding=(6, 7, 6, 2))
        telemetry.grid(row=2, column=0, sticky="ew")
        telemetry.columnconfigure(0, weight=1)
        ttk.Label(telemetry, textvariable=self.frame_info, style="Telemetry.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(telemetry, textvariable=self.measurement, style="Panel.TLabel", font=("Microsoft YaHei UI", 9, "bold")).grid(row=1, column=0, sticky="w", pady=(5, 0))

        filters = ttk.LabelFrame(right, text=" 深度后处理 ", padding=8)
        filters.pack(fill="x", pady=(0, 8))
        ttk.Checkbutton(filters, text="空间滤波（平滑边缘）", variable=self.v["spatial"]).pack(anchor="w")
        ttk.Checkbutton(filters, text="时间滤波（减少闪烁）", variable=self.v["temporal"]).pack(anchor="w")
        ttk.Checkbutton(filters, text="孔洞填充", variable=self.v["hole"]).pack(anchor="w")
        ttk.Label(filters, text="滤波配置在下次启动采集时生效", style="Hint.TLabel").pack(anchor="w", pady=(4, 0))

        controls = ttk.LabelFrame(right, text=" 相机参数 ", padding=8)
        controls.pack(fill="x", pady=(0, 8))
        controls.columnconfigure(1, weight=1)
        ttk.Checkbutton(controls, text="自动曝光", variable=self.v["auto_exposure"], command=self._toggle_exposure).grid(row=0, column=0, columnspan=2, sticky="w")
        self.exposure_entry = self._entry_row(controls, "曝光 (μs)", "exposure", 1)[1]
        self.gain_entry = self._entry_row(controls, "增益", "gain", 2)[1]
        self.emitter_check = ttk.Checkbutton(controls, text="红外发射器", variable=self.v["emitter"])
        self.emitter_check.grid(row=3, column=0, columnspan=2, sticky="w", pady=(3, 0))
        self.laser_entry = self._entry_row(controls, "激光功率", "laser", 4)[1]
        ttk.Button(controls, text="应用相机参数", command=self.apply_controls, style="Accent.TButton").grid(row=5, column=0, columnspan=2, sticky="ew", pady=(7, 0))
        self._toggle_exposure()

        output = ttk.LabelFrame(right, text=" 数据保存 ", padding=6)
        output.pack(fill="both", expand=True)
        out_row = ttk.Frame(output, style="Panel.TFrame")
        out_row.pack(fill="x")
        out_row.columnconfigure(0, weight=1)
        ttk.Entry(out_row, textvariable=self.v["output_dir"]).grid(row=0, column=0, sticky="ew", padx=(0, 4))
        ttk.Button(out_row, text="目录", command=self.choose_output_dir).grid(row=0, column=1)
        ttk.Button(output, text="保存当前帧", command=self.save_snapshot, style="Accent.TButton").pack(fill="x", pady=(5, 3))
        ttk.Button(output, text="导出三维点云 PLY", command=self.export_ply).pack(fill="x")
        ttk.Label(output, text="保存 PNG、原始 Z16 深度与 JSON 元数据", style="Hint.TLabel", wraplength=250).pack(anchor="w", pady=(3, 0))

        statusbar = ttk.Frame(self, style="Panel.TFrame")
        statusbar.grid(row=2, column=0, columnspan=3, sticky="ew", padx=12, pady=(8, 10))
        ttk.Label(statusbar, text="● SYSTEM", style="Status.TLabel").pack(side="left")
        ttk.Label(statusbar, textvariable=self.status, style="Status.TLabel").pack(side="left", fill="x", expand=True)

    @staticmethod
    def _preview_canvas(parent: Any) -> tk.Canvas:
        canvas = tk.Canvas(parent, bg=BG, highlightthickness=1, highlightbackground="#303030", cursor="crosshair")
        canvas.bind("<Configure>", lambda event, c=canvas: DepthCameraApp._draw_placeholder(c))
        return canvas

    @staticmethod
    def _draw_placeholder(canvas: tk.Canvas) -> None:
        if canvas.find_withtag("image"):
            return
        canvas.delete("placeholder")
        width, height = max(canvas.winfo_width(), 100), max(canvas.winfo_height(), 100)
        for x in range(20, width, 40):
            canvas.create_line(x, 0, x, height, fill="#0d1417", tags="placeholder")
        for y in range(20, height, 40):
            canvas.create_line(0, y, width, y, fill="#0d1417", tags="placeholder")
        canvas.create_text(width / 2, height / 2, text="NO SIGNAL", fill="#34454d", font=("Consolas", 16, "bold"), tags="placeholder")

    def _on_notebook_changed(self, _event: tk.Event | None = None) -> None:
        if self.notebook.select() == str(self.depth3d_tab):
            self.after_idle(self._render_depth_3d)

    def _on_3d_configure(self, _event: tk.Event | None = None) -> None:
        self.after_idle(self._render_depth_3d)

    def _start_3d_rotate(self, event: tk.Event) -> None:
        self.view3d_drag = (event.x, event.y, self.view3d_yaw, self.view3d_pitch)

    def _drag_3d_rotate(self, event: tk.Event) -> None:
        if self.view3d_drag is None:
            return
        start_x, start_y, start_yaw, start_pitch = self.view3d_drag
        self.view3d_yaw = start_yaw + (event.x - start_x) * 0.012
        self.view3d_pitch = min(
            math.radians(88.0),
            max(math.radians(-88.0), start_pitch + (event.y - start_y) * 0.012),
        )
        self._render_depth_3d()

    def _zoom_3d(self, event: tk.Event) -> None:
        factor = 1.12 if event.delta > 0 else 1.0 / 1.12
        self.view3d_zoom = min(4.0, max(0.35, self.view3d_zoom * factor))
        self._render_depth_3d()

    def _reset_3d_view(self, _event: tk.Event | None = None) -> None:
        self.view3d_yaw = math.radians(-35.0)
        self.view3d_pitch = math.radians(20.0)
        self.view3d_zoom = 1.0
        self.view3d_drag = None
        self._render_depth_3d()

    def _render_depth_3d(self) -> None:
        canvas = getattr(self, "depth3d_canvas", None)
        if canvas is None:
            return
        canvas_w = max(canvas.winfo_width(), 100)
        canvas_h = max(canvas.winfo_height(), 100)
        packet = self.current_frame
        if packet is None or packet.depth_raw is None or np is None or Image is None or ImageDraw is None or ImageTk is None:
            canvas.delete("all")
            canvas.create_text(
                canvas_w / 2,
                canvas_h / 2,
                text="NO DEPTH FRAME\n启动深度采集后可旋转查看",
                fill="#34454d",
                font=("Microsoft YaHei UI", 13, "bold"),
                justify="center",
            )
            return

        depth = packet.depth_raw
        depth_h, depth_w = depth.shape[:2]
        step = max(1, int(math.ceil(math.sqrt(depth_h * depth_w / 7000.0))))
        sampled = depth[::step, ::step].astype(np.float32) * float(packet.depth_scale)
        ys, xs = np.mgrid[0:depth_h:step, 0:depth_w:step]
        try:
            max_distance = float(self.v["max_distance"].get())
        except (TypeError, ValueError):
            max_distance = 4.0
        valid = (sampled > 0.0) & (sampled <= max_distance)
        if not np.any(valid):
            canvas.delete("all")
            canvas.create_text(
                canvas_w / 2,
                canvas_h / 2,
                text="NO VALID DEPTH",
                fill="#34454d",
                font=("Consolas", 16, "bold"),
            )
            return

        z = sampled[valid]
        image_x = xs[valid].astype(np.float32)
        image_y = ys[valid].astype(np.float32)
        intrinsics = packet.intrinsics
        fx = float(getattr(intrinsics, "fx", depth_w * 0.92))
        fy = float(getattr(intrinsics, "fy", depth_h * 1.22))
        ppx = float(getattr(intrinsics, "ppx", depth_w / 2.0))
        ppy = float(getattr(intrinsics, "ppy", depth_h / 2.0))
        point_x = (image_x - ppx) * z / max(fx, 1e-6)
        point_y = -(image_y - ppy) * z / max(fy, 1e-6)
        point_z = z - float(np.median(z))

        cos_yaw, sin_yaw = math.cos(self.view3d_yaw), math.sin(self.view3d_yaw)
        cos_pitch, sin_pitch = math.cos(self.view3d_pitch), math.sin(self.view3d_pitch)
        rotated_x = cos_yaw * point_x + sin_yaw * point_z
        rotated_z = -sin_yaw * point_x + cos_yaw * point_z
        rotated_y = cos_pitch * point_y - sin_pitch * rotated_z
        view_depth = sin_pitch * point_y + cos_pitch * rotated_z

        extent_values = np.concatenate((np.abs(rotated_x), np.abs(rotated_y)))
        extent = max(float(np.percentile(extent_values, 98.0)), 0.03)
        scale = min(canvas_w, canvas_h) * 0.43 * self.view3d_zoom / extent
        screen_x = np.rint(canvas_w / 2.0 + rotated_x * scale).astype(np.int32)
        screen_y = np.rint(canvas_h / 2.0 - rotated_y * scale).astype(np.int32)
        inside = (screen_x >= 1) & (screen_x < canvas_w - 1) & (screen_y >= 1) & (screen_y < canvas_h - 1)
        screen_x, screen_y, view_depth = screen_x[inside], screen_y[inside], view_depth[inside]

        color_source = packet.color_rgb if packet.color_rgb is not None else packet.depth_rgb
        if color_source is not None:
            color_h, color_w = color_source.shape[:2]
            color_x = np.minimum((image_x * color_w / depth_w).astype(np.int32), color_w - 1)
            color_y = np.minimum((image_y * color_h / depth_h).astype(np.int32), color_h - 1)
            colors = color_source[color_y, color_x][inside].astype(np.uint8)
        else:
            normalized = np.clip(z / max(max_distance, 0.001), 0.0, 1.0)[inside]
            colors = np.column_stack(
                (
                    (255.0 * normalized).astype(np.uint8),
                    (220.0 * (1.0 - normalized)).astype(np.uint8),
                    np.full(normalized.shape, 235, dtype=np.uint8),
                )
            )

        order = np.argsort(view_depth)
        screen_x, screen_y, colors = screen_x[order], screen_y[order], colors[order]
        raster = np.zeros((canvas_h, canvas_w, 3), dtype=np.uint8)
        raster[:, :, 0] = 2
        raster[:, :, 1] = 6
        raster[:, :, 2] = 8
        for offset_x, offset_y in ((0, 0), (1, 0), (0, 1), (1, 1)):
            px = np.clip(screen_x + offset_x, 0, canvas_w - 1)
            py = np.clip(screen_y + offset_y, 0, canvas_h - 1)
            raster[py, px] = colors

        image = Image.fromarray(raster, mode="RGB")
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, 0, canvas_w - 1, canvas_h - 1), outline="#26363d")
        draw.text(
            (10, 8),
            f"YAW {math.degrees(self.view3d_yaw):.0f}°  PITCH {math.degrees(self.view3d_pitch):.0f}°  ZOOM {self.view3d_zoom:.2f}×",
            fill="#8de8ff",
        )
        self._draw_3d_axes(draw, canvas_h)
        photo = ImageTk.PhotoImage(image)
        self.photos["depth3d"] = photo
        canvas.delete("all")
        canvas.create_image(0, 0, image=photo, anchor="nw")
        self.view3d_last_render = time.monotonic()

    def _draw_3d_axes(self, draw: Any, canvas_h: int) -> None:
        origin_x, origin_y = 48, canvas_h - 42
        axis_length = 34.0
        cos_yaw, sin_yaw = math.cos(self.view3d_yaw), math.sin(self.view3d_yaw)
        cos_pitch, sin_pitch = math.cos(self.view3d_pitch), math.sin(self.view3d_pitch)
        axes = (
            ("X", cos_yaw, -(sin_pitch * sin_yaw), "#ff5a67"),
            ("Y", 0.0, -cos_pitch, "#00f0b5"),
            ("Z", sin_yaw, sin_pitch * cos_yaw, "#40dcff"),
        )
        for label, direction_x, direction_y, color in axes:
            end_x = origin_x + direction_x * axis_length
            end_y = origin_y + direction_y * axis_length
            draw.line((origin_x, origin_y, end_x, end_y), fill=color, width=2)
            draw.text((end_x + 2, end_y - 7), label, fill=color)
        draw.ellipse((origin_x - 2, origin_y - 2, origin_x + 2, origin_y + 2), fill="#ffffff")

    def _combo_row(self, parent: Any, label: str, key: str, values: tuple[str, ...], row: int) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=(0, 7), pady=2)
        ttk.Combobox(parent, textvariable=self.v[key], values=values, state="readonly", width=15).grid(row=row, column=1, sticky="ew", pady=2)

    def _entry_row(self, parent: Any, label: str, key: str, row: int) -> tuple[Any, Any]:
        label_widget = ttk.Label(parent, text=label)
        entry = ttk.Entry(parent, textvariable=self.v[key], width=14)
        label_widget.grid(row=row, column=0, sticky="w", padx=(0, 7), pady=2)
        entry.grid(row=row, column=1, sticky="ew", pady=2)
        return label_widget, entry

    def _fit_to_screen(self) -> None:
        self.update_idletasks()
        screen_w, screen_h = self.winfo_screenwidth(), self.winfo_screenheight()
        width = max(1120, min(1480, screen_w - 32))
        height = 700
        self.geometry(f"{width}x{height}+{max(0, (screen_w-width)//2)}+{max(0, (screen_h-height)//2-15)}")

    def refresh_devices(self) -> None:
        if self.busy or (self.controller and self.controller.running):
            return
        self.status.set("正在枚举深度相机…")
        simulation = bool(self.v["simulation"].get())

        def worker() -> None:
            try:
                devices = DepthCameraController.list_devices(include_simulated=simulation)
                self.events.put(("devices", devices))
            except Exception as exc:
                self.events.put(("error", str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def start_camera(self) -> None:
        if self.busy:
            return
        if Image is None or ImageTk is None:
            messagebox.showerror("缺少运行依赖", "缺少 Pillow，无法显示图像。请先双击 install.bat。")
            return
        try:
            settings = self._settings()
            record_path = self._record_path()
        except (ValueError, CameraError) as exc:
            messagebox.showerror("采集参数错误", str(exc))
            return
        serial = ""
        selected = str(self.v["device"].get())
        for device in self.devices:
            if device.label == selected:
                serial = device.serial
                break
        simulate = bool(self.v["simulation"].get())
        source_file = "" if simulate else str(self.v["source_file"].get()).strip()
        if simulate and record_path:
            messagebox.showerror("录像不可用", "仿真模式不生成 RealSense SDK 原始录像；请取消录像或切换到真实设备。")
            return
        if source_file and record_path:
            messagebox.showerror("采集模式冲突", "回放录像时不能同时录制，请取消“同步录像”。")
            return
        self.controller = DepthCameraController(simulate=simulate)
        self._set_busy(True, "正在启动深度相机…")

        def worker() -> None:
            try:
                info = self.controller.start(
                    settings,
                    serial=serial,
                    source_file=source_file,
                    record_path=record_path,
                    on_frame=self._receive_frame,
                    on_error=lambda message: self.events.put(("stream_error", message)),
                )
                controls = self.controller.control_state()
                self.events.put(("started", (info, controls, record_path)))
            except Exception as exc:
                self.events.put(("start_error", str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def _settings(self) -> CameraSettings:
        width_text, height_text = str(self.v["resolution"].get()).split("×")
        return CameraSettings(
            width=int(width_text.strip()),
            height=int(height_text.strip()),
            fps=int(str(self.v["fps"].get())),
            enable_color=bool(self.v["color"].get()),
            enable_depth=bool(self.v["depth"].get()),
            enable_infrared=bool(self.v["infrared"].get()),
            align_depth=bool(self.v["align"].get()),
            spatial_filter=bool(self.v["spatial"].get()),
            temporal_filter=bool(self.v["temporal"].get()),
            hole_filling_filter=bool(self.v["hole"].get()),
            max_distance_m=float(str(self.v["max_distance"].get())),
        )

    def _record_path(self) -> str:
        if not self.v["record"].get():
            return ""
        value = str(self.v["record_file"].get()).strip()
        if not value:
            directory = Path(str(self.v["output_dir"].get())) / "recordings"
            value = str(directory / f"realsense_{datetime.now():%Y%m%d_%H%M%S}.db3")
            self.v["record_file"].set(value)
        if Path(value).suffix.lower() not in (".db3", ".bag"):
            raise CameraError("录像文件扩展名应为 .db3（SDK 2.58）或兼容的 .bag")
        return value

    def _receive_frame(self, packet: FramePacket) -> None:
        self.pending_frame = packet

    def stop_camera(self) -> None:
        controller = self.controller
        if not controller or self.busy:
            return
        self._set_busy(True, "正在停止采集并封装录像文件…")

        def worker() -> None:
            controller.stop()
            self.events.put(("stopped", None))

        threading.Thread(target=worker, daemon=True).start()

    def apply_controls(self) -> None:
        if not self.controller or not self.controller.running:
            messagebox.showinfo("相机参数", "请先启动相机。")
            return
        try:
            values = (
                bool(self.v["auto_exposure"].get()),
                float(str(self.v["exposure"].get())),
                float(str(self.v["gain"].get())),
                bool(self.v["emitter"].get()),
                float(str(self.v["laser"].get())),
            )
        except ValueError:
            messagebox.showerror("参数错误", "曝光、增益和激光功率必须是有效数字。")
            return

        def worker() -> None:
            try:
                self.controller.apply_controls(*values)
                self.events.put(("notice", "相机参数已应用"))
            except Exception as exc:
                self.events.put(("error", str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def save_snapshot(self) -> None:
        packet = self.current_frame
        if packet is None:
            messagebox.showinfo("保存当前帧", "当前没有图像帧。")
            return
        if Image is None:
            return
        try:
            directory = Path(str(self.v["output_dir"].get())).expanduser()
            directory.mkdir(parents=True, exist_ok=True)
            stem = f"frame_{datetime.now():%Y%m%d_%H%M%S_%f}"[:-3]
            saved: list[str] = []
            for suffix, image_data in (("color", packet.color_rgb), ("depth", packet.depth_rgb), ("infrared", packet.infrared_rgb)):
                if image_data is not None:
                    path = directory / f"{stem}_{suffix}.png"
                    Image.fromarray(image_data).save(path)
                    saved.append(path.name)
            if packet.depth_raw is not None:
                path = directory / f"{stem}_depth_z16.png"
                Image.fromarray(packet.depth_raw).save(path)
                saved.append(path.name)
            metadata = self.controller.metadata() if self.controller else {}
            metadata["saved_files"] = saved
            meta_path = directory / f"{stem}_metadata.json"
            meta_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
            saved.append(meta_path.name)
            self._log(f"已保存帧组：{', '.join(saved)}")
            self.status.set(f"当前帧已保存到 {directory}")
        except Exception as exc:
            messagebox.showerror("保存失败", str(exc))

    def export_ply(self) -> None:
        if not self.controller or self.controller.latest_frame() is None:
            messagebox.showinfo("导出点云", "请先启动采集并等待深度画面。")
            return
        directory = Path(str(self.v["output_dir"].get()))
        default = f"pointcloud_{datetime.now():%Y%m%d_%H%M%S}.ply"
        path = filedialog.asksaveasfilename(title="导出三维点云", initialdir=str(directory), initialfile=default, defaultextension=".ply", filetypes=[("PLY 点云", "*.ply")])
        if not path:
            return
        self.status.set("正在导出点云…")

        def worker() -> None:
            try:
                self.controller.export_ply(path)
                self.events.put(("notice", f"点云已导出：{path}"))
            except Exception as exc:
                self.events.put(("error", str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def _measure_click(self, event: tk.Event, source: str = "depth") -> None:
        packet = self.current_frame
        geometry = self.display_geometry.get(source)
        if not self.controller or packet is None or packet.depth_raw is None or geometry is None:
            return
        if source == "color" and not self.controller.settings.align_depth:
            text = "彩色图定位需要启用“深度对齐到彩色画面”，然后重新启动采集。"
            self.measurement.set(text)
            self.status.set(text)
            return
        left, top, shown_w, shown_h, image_w, image_h = geometry
        if not (left <= event.x < left + shown_w and top <= event.y < top + shown_h):
            return
        source_x = (event.x - left) * image_w / shown_w
        source_y = (event.y - top) * image_h / shown_h
        depth_h, depth_w = packet.depth_raw.shape[:2]
        pixel_x = int(source_x * depth_w / image_w)
        pixel_y = int(source_y * depth_h / image_h)
        try:
            result = self.controller.measure(pixel_x, pixel_y, packet=packet, search_radius=4)
            prefix = "彩色图定位 → " if source == "color" else "深度图定位 → "
            self.measurement.set(prefix + result.display)
            self.crosshair = (result.pixel_x, result.pixel_y)
            self._render_image("color", self.color_canvas, packet.color_rgb)
            self._render_image("depth", self.depth_canvas, packet.depth_rgb)
            self._log(prefix + result.display.replace("\n", " | "))
        except CameraError as exc:
            self.measurement.set(str(exc))

    def _poll(self) -> None:
        try:
            while True:
                kind, payload = self.events.get_nowait()
                self._handle_event(kind, payload)
        except queue.Empty:
            pass
        packet, self.pending_frame = self.pending_frame, None
        if packet is not None:
            self.current_frame = packet
            self._render_packet(packet)
        self.after(40, self._poll)

    def _handle_event(self, kind: str, payload: Any) -> None:
        if kind == "devices":
            self.devices = payload
            labels = [device.label for device in self.devices]
            self.device_combo.configure(values=labels)
            if labels:
                self.v["device"].set(labels[0])
                real_count = len([d for d in self.devices if not d.simulated])
                self.status.set(f"检测到 {real_count} 台 RealSense 设备" + ("；仿真设备可用" if self.v["simulation"].get() else ""))
            else:
                self.v["device"].set("")
                self.status.set("未检测到相机；请检查 USB 3.x 连接，或启用仿真模式")
        elif kind == "started":
            info, controls, record_path = payload
            self._set_busy(False, f"采集已启动：{info.name}")
            self.start_button.configure(state="disabled")
            self.stop_button.configure(state="normal")
            self.device_info.set(f"设备：{info.name} · SN {info.serial} · USB {info.usb_type or '--'}")
            self.record_state.set(f"● 正在录像：{record_path}" if record_path else "未录像")
            self._load_controls(controls)
            self._log(f"连接成功 | {info.label} | FW {info.firmware or '--'} | USB {info.usb_type or '--'}")
        elif kind == "start_error":
            self._set_busy(False, "相机启动失败")
            if self.controller:
                self.controller.stop()
            messagebox.showerror("启动失败", payload)
            self._log(f"ERROR | {payload}")
        elif kind == "stopped":
            self._set_busy(False, "采集已停止；录像文件已完成写入")
            self.start_button.configure(state="normal")
            self.stop_button.configure(state="disabled")
            self.record_state.set("未录像")
            self.device_info.set("设备：未连接")
            self._log("采集已停止")
        elif kind == "stream_error":
            self.status.set(f"数据流异常：{payload}")
            self.start_button.configure(state="normal")
            self.stop_button.configure(state="disabled")
            self._log(f"STREAM ERROR | {payload}")
            if self.controller:
                threading.Thread(target=self.controller.stop, daemon=True).start()
            messagebox.showerror("数据流异常", payload)
        elif kind == "error":
            self.status.set(payload)
            self._log(f"ERROR | {payload}")
            messagebox.showerror("操作失败", payload)
        elif kind == "notice":
            self.status.set(payload)
            self._log(payload)

    def _render_packet(self, packet: FramePacket) -> None:
        self._render_image("color", self.color_canvas, packet.color_rgb)
        self._render_image("depth", self.depth_canvas, packet.depth_rgb)
        self._render_image("infrared", self.infrared_canvas, packet.infrared_rgb)
        if self.notebook.select() == str(self.depth3d_tab) and time.monotonic() - self.view3d_last_render >= 0.12:
            self._render_depth_3d()
        self.frame_info.set(f"FRAME {packet.frame_number:06d}  |  {packet.fps:4.1f} FPS  |  {packet.timestamp_ms:10.2f} ms  |  SCALE {packet.depth_scale:g} m")

    def _render_image(self, key: str, canvas: tk.Canvas, data: Any | None) -> None:
        if data is None or Image is None or ImageTk is None:
            self._draw_placeholder(canvas)
            return
        image_h, image_w = data.shape[:2]
        canvas_w, canvas_h = max(canvas.winfo_width(), 80), max(canvas.winfo_height(), 80)
        scale = min(canvas_w / image_w, canvas_h / image_h)
        shown_w, shown_h = max(1, int(image_w * scale)), max(1, int(image_h * scale))
        left, top = (canvas_w - shown_w) // 2, (canvas_h - shown_h) // 2
        image = Image.fromarray(data)
        if (shown_w, shown_h) != (image_w, image_h):
            image = image.resize((shown_w, shown_h), Image.Resampling.BILINEAR)
        photo = ImageTk.PhotoImage(image)
        self.photos[key] = photo
        canvas.delete("all")
        canvas.create_image(left, top, image=photo, anchor="nw", tags="image")
        canvas.create_rectangle(left, top, left + shown_w - 1, top + shown_h - 1, outline="#26363d")
        self.display_geometry[key] = (left, top, shown_w, shown_h, image_w, image_h)
        if key in ("color", "depth") and self.crosshair and self.current_frame is not None and self.current_frame.depth_raw is not None:
            depth_h, depth_w = self.current_frame.depth_raw.shape[:2]
            marker_x = self.crosshair[0] * image_w / depth_w
            marker_y = self.crosshair[1] * image_h / depth_h
            x = left + marker_x * shown_w / image_w
            y = top + marker_y * shown_h / image_h
            canvas.create_oval(x - 8, y - 8, x + 8, y + 8, outline="#fff200", width=2)
            canvas.create_line(x - 14, y, x + 14, y, fill="#fff200")
            canvas.create_line(x, y - 14, x, y + 14, fill="#fff200")

    def _load_controls(self, controls: dict[str, Any]) -> None:
        if not controls:
            return
        for key, target in (("auto_exposure", "auto_exposure"), ("exposure", "exposure"), ("gain", "gain"), ("emitter", "emitter"), ("laser_power", "laser")):
            value = controls.get(key)
            if value is not None:
                self.v[target].set(value)
        emitter_supported = bool(controls.get("emitter_supported", False))
        laser_supported = controls.get("laser_range") is not None
        self.emitter_check.configure(state="normal" if emitter_supported else "disabled")
        self.laser_entry.configure(state="normal" if laser_supported else "disabled")
        if not emitter_supported and not laser_supported:
            self._log("当前设备不支持红外发射器和激光功率控制（D405 等被动双目型号属于正常情况）")
        self._toggle_exposure()

    def _toggle_exposure(self) -> None:
        state = "disabled" if self.v["auto_exposure"].get() else "normal"
        if hasattr(self, "exposure_entry"):
            self.exposure_entry.configure(state=state)
            self.gain_entry.configure(state=state)

    def _set_busy(self, busy: bool, text: str) -> None:
        self.busy = busy
        self.status.set(text)
        if busy:
            self.start_button.configure(state="disabled")
            self.stop_button.configure(state="disabled")
        elif not (self.controller and self.controller.running):
            self.start_button.configure(state="normal")

    def _log(self, message: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert("end", f"[{datetime.now():%H:%M:%S}] {message}\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def choose_source(self) -> None:
        path = filedialog.askopenfilename(title="选择 RealSense 录像", filetypes=[("RealSense 录像", "*.db3 *.bag"), ("所有文件", "*.*")])
        if path:
            self.v["source_file"].set(path)
            self.v["simulation"].set(False)
            self.refresh_devices()

    def choose_record_file(self) -> None:
        path = filedialog.asksaveasfilename(title="保存原始相机录像", defaultextension=".db3", filetypes=[("RealSense DB3", "*.db3"), ("Legacy BAG", "*.bag")])
        if path:
            self.v["record_file"].set(path)
            self.v["record"].set(True)

    def choose_output_dir(self) -> None:
        path = filedialog.askdirectory(title="选择采集数据保存目录", initialdir=str(self.v["output_dir"].get()))
        if path:
            self.v["output_dir"].set(path)

    def _on_close(self) -> None:
        if self.controller:
            self.controller.stop()
        self.destroy()


def run() -> None:
    app = DepthCameraApp()
    status = DepthCameraController.runtime_status()
    missing = [name for name, available in status.items() if not available]
    if Image is None:
        missing.append("pillow")
    if missing:
        app.status.set("缺少运行依赖：" + ", ".join(missing) + "；请先双击 install.bat")
    app.mainloop()
