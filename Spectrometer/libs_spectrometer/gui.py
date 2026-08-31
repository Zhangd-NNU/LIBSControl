from __future__ import annotations

import csv
import itertools
import math
import queue
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .controller import (
    NetworkDevice,
    SeaSDKController,
    SimulationController,
    SpectrometerError,
    discover_network_devices,
)
from .processing import Frame, Spectrum, build_processed_frame


CHANNEL_COLORS = ("#40dcff", "#ffbd4a", "#d16bff", "#46e6a9")
TRIGGER_MODES = {
    "内触发（正常模式）": 0,
    "外触发（上升沿）": 2,
    "异步复位（低延迟）": 6,
}
STITCH_MODES = {
    "原始强度直接拼接": "raw",
    "重叠区自动校正": "auto",
    "标定系数拼接": "calibrated",
}


def save_frame_csv(frame: Frame, path: str | Path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    spectra = list(frame.displayed)
    columns: list[tuple[str, tuple[float, ...]]] = []
    for spectrum in spectra:
        columns.extend(
            [
                (f"ch{spectrum.channel}_wavelength_nm", spectrum.wavelengths),
                (f"ch{spectrum.channel}_intensity_count", spectrum.intensities),
            ]
        )
    if frame.stitched:
        columns.extend(
            [
                ("stitched_wavelength_nm", frame.stitched.wavelengths),
                ("stitched_intensity_count", frame.stitched.intensities),
            ]
        )
    with target.open("w", newline="", encoding="utf-8-sig") as stream:
        stream.write(f"# captured_at={frame.captured_at.isoformat(timespec='milliseconds')}\n")
        stream.write(f"# sequence={frame.sequence}\n")
        stream.write(f"# dark_subtracted={int(frame.dark_subtracted)}\n")
        for key, value in frame.metadata.items():
            stream.write(f"# {key}={value}\n")
        writer = csv.writer(stream)
        writer.writerow([name for name, _ in columns])
        for row in itertools.zip_longest(*(values for _, values in columns), fillvalue=""):
            writer.writerow(row)
    return target


class SpectrumCanvas(tk.Canvas):
    def __init__(self, master, **kwargs):
        super().__init__(master, bg="#080d11", highlightthickness=1, highlightbackground="#22343d", **kwargs)
        self.frame_data: Frame | None = None
        self.display_mode = "四通道叠加 + 拼接"
        self.selected_channel = 1
        self.show_peaks = True
        self.x_window: tuple[float, float] | None = None
        self._series: list[tuple[str, Spectrum, str, int]] = []
        self._plot_box = (65, 25, 300, 200)
        self._last_cursor_x: float | None = None
        self.bind("<Configure>", lambda _: self.redraw())
        self.bind("<MouseWheel>", self._zoom)
        self.bind("<Button-3>", lambda _: self.reset_view())
        self.bind("<Motion>", self._motion)
        self.bind("<Leave>", lambda _: self.delete("cursor"))

    def set_frame(self, frame: Frame | None) -> None:
        self.frame_data = frame
        self.x_window = None
        self.redraw()

    def set_options(self, mode: str, channel: int, show_peaks: bool) -> None:
        self.display_mode, self.selected_channel, self.show_peaks = mode, channel, show_peaks
        self.redraw()

    def reset_view(self) -> None:
        self.x_window = None
        self.redraw()

    def _visible_series(self) -> list[tuple[str, Spectrum, str, int]]:
        if not self.frame_data:
            return []
        channels = [
            (f"CH{s.channel}", s, CHANNEL_COLORS[(s.channel - 1) % len(CHANNEL_COLORS)], 1)
            for s in self.frame_data.displayed
        ]
        stitched = []
        if self.frame_data.stitched:
            stitched = [("拼接", self.frame_data.stitched, "#f4f7f8", 2)]
        if self.display_mode == "仅拼接光谱":
            return stitched
        if self.display_mode == "仅四通道":
            return channels
        if self.display_mode == "仅当前通道":
            return [item for item in channels if item[1].channel == self.selected_channel]
        return channels + stitched

    def _bounds(self, series: list[tuple[str, Spectrum, str, int]]) -> tuple[float, float, float, float] | None:
        if not series:
            return None
        xmin = min(min(s.wavelengths) for _, s, _, _ in series if s.wavelengths)
        xmax = max(max(s.wavelengths) for _, s, _, _ in series if s.wavelengths)
        if self.x_window:
            xmin, xmax = self.x_window
        visible: list[float] = []
        for _, spectrum, _, _ in series:
            visible.extend(y for x, y in zip(spectrum.wavelengths, spectrum.intensities) if xmin <= x <= xmax)
        if not visible:
            return None
        ymin = min(0.0, min(visible))
        ymax = max(1.0, max(visible)) * 1.08
        return xmin, xmax, ymin, ymax

    def redraw(self) -> None:
        self.delete("all")
        width, height = max(300, self.winfo_width()), max(220, self.winfo_height())
        left, top, right, bottom = 68, 28, width - 22, height - 50
        self._plot_box = (left, top, right, bottom)
        self.create_rectangle(left, top, right, bottom, outline="#22343d", fill="#080d11")
        series = self._visible_series()
        self._series = series
        bounds = self._bounds(series)
        if not bounds:
            self.create_text(width / 2, height / 2 - 8, text="等待光谱数据", fill="#7b929d", font=("Microsoft YaHei UI", 14, "bold"))
            self.create_text(width / 2, height / 2 + 22, text="连接设备后，选择单次、定次或连续采集", fill="#465a64", font=("Microsoft YaHei UI", 9))
            self._draw_labels(left, top, right, bottom, 0, 1, 0, 1)
            return
        xmin, xmax, ymin, ymax = bounds
        self._draw_grid(left, top, right, bottom, xmin, xmax, ymin, ymax)
        transform = lambda x, y: (
            left + (x - xmin) / max(1e-12, xmax - xmin) * (right - left),
            bottom - (y - ymin) / max(1e-12, ymax - ymin) * (bottom - top),
        )
        for name, spectrum, color, line_width in series:
            points: list[float] = []
            step = max(1, len(spectrum.wavelengths) // max(400, width * 2))
            for x, y in zip(spectrum.wavelengths[::step], spectrum.intensities[::step]):
                if xmin <= x <= xmax:
                    px, py = transform(x, y)
                    points.extend((px, py))
            if len(points) >= 4:
                self.create_line(*points, fill=color, width=line_width, smooth=False)
        self._draw_legend(series, right, top)
        if self.show_peaks:
            self._draw_peaks(series, transform, xmin, xmax, top, bottom)
        self._draw_labels(left, top, right, bottom, xmin, xmax, ymin, ymax)

    def _draw_grid(self, left: int, top: int, right: int, bottom: int, xmin: float, xmax: float, ymin: float, ymax: float) -> None:
        for i in range(1, 6):
            x = left + (right - left) * i / 6
            self.create_line(x, top, x, bottom, fill="#142129")
        for i in range(1, 5):
            y = top + (bottom - top) * i / 5
            self.create_line(left, y, right, y, fill="#142129")

    def _draw_labels(self, left: int, top: int, right: int, bottom: int, xmin: float, xmax: float, ymin: float, ymax: float) -> None:
        for i in range(7):
            x = left + (right - left) * i / 6
            value = xmin + (xmax - xmin) * i / 6
            self.create_text(x, bottom + 17, text=f"{value:.0f}", fill="#728791", font=("Segoe UI", 8))
        for i in range(6):
            y = bottom - (bottom - top) * i / 5
            value = ymin + (ymax - ymin) * i / 5
            label = f"{value/1000:.1f}k" if abs(value) >= 1000 else f"{value:.0f}"
            self.create_text(left - 9, y, text=label, fill="#728791", anchor="e", font=("Segoe UI", 8))
        self.create_text((left + right) / 2, bottom + 37, text="波长 (nm)", fill="#a9b8be", font=("Microsoft YaHei UI", 9))
        self.create_text(15, (top + bottom) / 2, text="强\n度\n(count)", fill="#a9b8be", font=("Microsoft YaHei UI", 8), justify="center")

    def _draw_legend(self, series, right: int, top: int) -> None:
        x = right - 8
        for name, _, color, _ in reversed(series):
            width = 32 + len(name) * 8
            self.create_line(x - width + 5, top + 12, x - width + 20, top + 12, fill=color, width=2)
            self.create_text(x - width + 24, top + 12, text=name, fill="#bdc9ce", anchor="w", font=("Microsoft YaHei UI", 8))
            x -= width

    def _draw_peaks(self, series, transform, xmin: float, xmax: float, top: int, bottom: int) -> None:
        source = next((s for name, s, _, _ in series if name == "拼接"), series[-1][1] if series else None)
        if not source or len(source.intensities) < 3:
            return
        candidates: list[tuple[float, int]] = []
        values = source.intensities
        threshold = max(values) * 0.12 if values else 0
        for i in range(1, len(values) - 1):
            if xmin <= source.wavelengths[i] <= xmax and values[i] >= threshold and values[i] > values[i - 1] and values[i] >= values[i + 1]:
                candidates.append((values[i], i))
        chosen: list[int] = []
        for _, index in sorted(candidates, reverse=True):
            if all(abs(source.wavelengths[index] - source.wavelengths[other]) > (xmax - xmin) * 0.035 for other in chosen):
                chosen.append(index)
            if len(chosen) == 6:
                break
        for index in sorted(chosen):
            x, y = transform(source.wavelengths[index], source.intensities[index])
            if top + 18 < y < bottom:
                self.create_line(x, y, x, max(top + 16, y - 24), fill="#62757d", dash=(2, 2))
                self.create_text(x, max(top + 10, y - 29), text=f"{source.wavelengths[index]:.2f}", fill="#dbe5e8", font=("Segoe UI", 8))

    def _zoom(self, event) -> None:
        series = self._visible_series()
        bounds = self._bounds(series)
        if not bounds:
            return
        xmin, xmax, _, _ = bounds
        left, _, right, _ = self._plot_box
        if not left <= event.x <= right:
            return
        center = xmin + (event.x - left) / (right - left) * (xmax - xmin)
        factor = 0.78 if event.delta > 0 else 1.28
        full_min = min(min(s.wavelengths) for _, s, _, _ in series)
        full_max = max(max(s.wavelengths) for _, s, _, _ in series)
        new_min = max(full_min, center - (center - xmin) * factor)
        new_max = min(full_max, center + (xmax - center) * factor)
        self.x_window = None if new_max - new_min >= (full_max - full_min) * 0.99 else (new_min, new_max)
        self.redraw()

    def _motion(self, event) -> None:
        self.delete("cursor")
        if not self._series:
            return
        left, top, right, bottom = self._plot_box
        if not (left <= event.x <= right and top <= event.y <= bottom):
            return
        bounds = self._bounds(self._series)
        if not bounds:
            return
        xmin, xmax, _, _ = bounds
        wavelength = xmin + (event.x - left) / (right - left) * (xmax - xmin)
        source = next((s for name, s, _, _ in self._series if name == "拼接"), self._series[-1][1])
        nearest = min(range(len(source.wavelengths)), key=lambda i: abs(source.wavelengths[i] - wavelength))
        text = f"{source.wavelengths[nearest]:.3f} nm   {source.intensities[nearest]:.1f} count"
        self.create_line(event.x, top, event.x, bottom, fill="#4d6972", dash=(3, 3), tags="cursor")
        anchor = "ne" if event.x > (left + right) / 2 else "nw"
        tx = event.x - 7 if anchor == "ne" else event.x + 7
        self.create_text(tx, top + 8, text=text, fill="#d9f7ff", anchor=anchor, font=("Consolas", 9), tags="cursor")


class MariaApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("LIBS SPECTRUM CONTROL · Maria 四通道光谱仪")
        self.geometry("1440x860")
        self.minsize(1120, 700)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.events: queue.Queue = queue.Queue()
        self.controller: SeaSDKController | SimulationController | None = None
        self.current_frame: Frame | None = None
        self.dark_spectra: dict[int, Spectrum] = {}
        self.history: list[Frame] = []
        self.running = False
        self.stop_event = threading.Event()
        self.sequence = 0
        self._capture_dark = False
        self.plot_settings_visible = False
        self._build_vars()
        self._style()
        self._build_ui()
        self.bind("<F5>", lambda _: self.start_capture(1))
        self.bind("<Escape>", lambda _: self.stop_capture())
        self.bind("<Control-s>", lambda _: self.save_current())
        self.after_idle(self._fit_to_screen)
        self.after(70, self._poll_events)

    def _build_vars(self) -> None:
        source_root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
        storage_root = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else source_root
        defaults: dict[str, object] = {
            "simulation": True,
            "connection": "USB",
            "dll": str(source_root / "vendor" / "SeaSDK.dll"),
            "network_devices": "OH7020@24.5.10.30, OH7020@24.5.10.31, OH7020@24.5.10.32, OH7020@24.5.10.33",
            "port": "8888",
            "integration_us": "2000",
            "averages": "1",
            "hardware_boxcar": "0",
            "software_boxcar": "0",
            "delay_us": "2.0",
            "trigger": "内触发（正常模式）",
            "frame_count": "10",
            "interval_ms": "20",
            "subtract_dark": True,
            "auto_save": False,
            "save_dir": str(storage_root / "data"),
            "display": "四通道叠加 + 拼接",
            "channel": "通道 1",
            "show_peaks": True,
            "stitch_mode": "重叠区自动校正",
            "calibration_factors": "1.000, 1.000, 1.000, 1.000",
        }
        self.v: dict[str, tk.Variable] = {}
        for key, value in defaults.items():
            self.v[key] = tk.BooleanVar(value=value) if isinstance(value, bool) else tk.StringVar(value=value)
        self.status = tk.StringVar(value="未连接 · 可勾选仿真模式完整试运行")
        self.device_summary = tk.StringVar(value="0 / 4 CHANNELS ONLINE")
        self.frame_summary = tk.StringVar(value="帧 0   峰值 —   饱和度 —")
        self.metric_peak_wavelength = tk.StringVar(value="— nm")
        self.metric_peak_intensity = tk.StringVar(value="— count")
        self.metric_saturation = tk.StringVar(value="— %")
        self.metric_rate = tk.StringVar(value="— fps")
        self.stitch_hint = tk.StringVar(value="利用相邻通道重叠区自动对齐强度，适合快速观察完整谱线。")
        self.progress = tk.DoubleVar(value=0)

    def _style(self) -> None:
        self.configure(bg="#06090d")
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TFrame", background="#06090d")
        style.configure("Workspace.TFrame", background="#06090d")
        style.configure("Panel.TFrame", background="#0d1318")
        style.configure("Raised.TFrame", background="#121b22")
        style.configure("MetricCard.TFrame", background="#101820", bordercolor="#22323d", borderwidth=1, relief="solid")
        style.configure("TLabel", background="#0d1318", foreground="#d7e1e7", font=("Microsoft YaHei UI", 9))
        style.configure("Title.TLabel", background="#06090d", foreground="#f3f8fa", font=("Microsoft YaHei UI", 19, "bold"))
        style.configure("Brand.TLabel", background="#123240", foreground="#42dcff", font=("Segoe UI", 16, "bold"), padding=(10, 7))
        style.configure("SubTitle.TLabel", background="#06090d", foreground="#70828c", font=("Microsoft YaHei UI", 9))
        style.configure("Section.TLabel", background="#0d1318", foreground="#e6eef2", font=("Microsoft YaHei UI", 11, "bold"))
        style.configure("Eyebrow.TLabel", background="#0d1318", foreground="#55dfff", font=("Segoe UI", 8, "bold"))
        style.configure("MetricName.TLabel", background="#101820", foreground="#6f838e", font=("Microsoft YaHei UI", 8))
        style.configure("MetricValue.TLabel", background="#101820", foreground="#e8f7fb", font=("Consolas", 12, "bold"))
        style.configure("Metric.TLabel", background="#0d1318", foreground="#8de8ff", font=("Consolas", 11, "bold"))
        style.configure("Hint.TLabel", background="#0d1318", foreground="#71838d", font=("Microsoft YaHei UI", 8))
        style.configure("RaisedHint.TLabel", background="#121b22", foreground="#82949d", font=("Microsoft YaHei UI", 8))
        style.configure("Status.TLabel", background="#0a1014", foreground="#a9bac2", padding=(10, 8), font=("Microsoft YaHei UI", 9))
        style.configure("StatusDot.TLabel", background="#0a1014", foreground="#2fe3a0", padding=(12, 8, 0, 8), font=("Segoe UI", 10, "bold"))
        style.configure("Badge.TLabel", background="#10252b", foreground="#66e7ff", padding=(10, 5), font=("Segoe UI", 8, "bold"))
        style.configure("TLabelframe", background="#0d1318", bordercolor="#22313a", borderwidth=1, relief="solid")
        style.configure("TLabelframe.Label", background="#0d1318", foreground="#55dfff", font=("Microsoft YaHei UI", 10, "bold"), padding=(2, 0, 2, 4))
        style.configure("TEntry", fieldbackground="#111a20", foreground="#edf5f7", bordercolor="#2a3942", lightcolor="#2a3942", darkcolor="#2a3942", insertcolor="#46dcff", padding=(7, 6))
        style.map("TEntry", bordercolor=[("focus", "#2bb9da")], lightcolor=[("focus", "#2bb9da")], darkcolor=[("focus", "#2bb9da")])
        style.configure("TCombobox", fieldbackground="#111a20", foreground="#edf5f7", arrowcolor="#55dfff", bordercolor="#2a3942", padding=(7, 5))
        style.map("TCombobox", fieldbackground=[("readonly", "#111a20")], foreground=[("readonly", "#edf5f7")], selectbackground=[("readonly", "#111a20")], bordercolor=[("focus", "#2bb9da")])
        self.option_add("*TCombobox*Listbox.background", "#111a20")
        self.option_add("*TCombobox*Listbox.foreground", "#edf5f7")
        self.option_add("*TCombobox*Listbox.selectBackground", "#14758a")
        self.option_add("*TCombobox*Listbox.selectForeground", "#ffffff")
        style.configure("TCheckbutton", background="#0d1318", foreground="#bdcbd1", font=("Microsoft YaHei UI", 9))
        style.map("TCheckbutton", background=[("active", "#0d1318")], foreground=[("active", "#ffffff")])
        style.configure("TButton", background="#1a252c", foreground="#dce8ec", bordercolor="#2b3a42", borderwidth=1, padding=(11, 7), font=("Microsoft YaHei UI", 9))
        style.map("TButton", background=[("active", "#273740"), ("pressed", "#11191e")], bordercolor=[("focus", "#3b7382")])
        style.configure("Accent.TButton", background="#22bfe3", foreground="#041116", bordercolor="#22bfe3", padding=(12, 8), font=("Microsoft YaHei UI", 9, "bold"))
        style.map("Accent.TButton", background=[("active", "#54dcf7"), ("pressed", "#1598b7")])
        style.configure("Success.TButton", background="#1dbb83", foreground="#03140e", bordercolor="#1dbb83", padding=(12, 9), font=("Microsoft YaHei UI", 10, "bold"))
        style.map("Success.TButton", background=[("active", "#45d9a4"), ("pressed", "#13875f")])
        style.configure("Danger.TButton", background="#a93851", foreground="#ffffff", bordercolor="#c24761", padding=(11, 7))
        style.map("Danger.TButton", background=[("active", "#d44b67"), ("pressed", "#7f293d")])
        style.configure("Tech.Horizontal.TProgressbar", troughcolor="#172127", background="#2fe3a0", bordercolor="#172127", thickness=6)
        style.configure("Treeview", background="#0e161b", fieldbackground="#0e161b", foreground="#cbd8de", rowheight=27, bordercolor="#22313a", font=("Microsoft YaHei UI", 8))
        style.configure("Treeview.Heading", background="#162128", foreground="#65dff5", relief="flat", font=("Microsoft YaHei UI", 8, "bold"), padding=(4, 5))
        style.map("Treeview", background=[("selected", "#155f71")], foreground=[("selected", "#ffffff")])
        style.configure("TNotebook", background="#0d1318", bordercolor="#22313a", tabmargins=(0, 4, 0, 0))
        style.configure("TNotebook.Tab", background="#121b22", foreground="#82949d", padding=(16, 7), font=("Microsoft YaHei UI", 9))
        style.map("TNotebook.Tab", background=[("selected", "#18303a"), ("active", "#17252d")], foreground=[("selected", "#64e0f7"), ("active", "#d8e7ec")])

    def _fit_to_screen(self) -> None:
        self.update_idletasks()
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        width, height = max(1120, min(1440, sw - 32)), max(700, min(860, sh - 80))
        self.geometry(f"{width}x{height}+{max(0, (sw-width)//2)}+{max(0, (sh-height)//2-10)}")

    def _build_ui(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)
        header = ttk.Frame(self, style="Workspace.TFrame", padding=(18, 13, 18, 11))
        header.grid(row=0, column=0, sticky="ew")
        ttk.Label(header, text="M4", style="Brand.TLabel").pack(side="left")
        title_stack = ttk.Frame(header, style="Workspace.TFrame")
        title_stack.pack(side="left", padx=(13, 0))
        ttk.Label(title_stack, text="Maria Spectrum Studio", style="Title.TLabel").pack(anchor="w")
        ttk.Label(title_stack, text="四通道光谱采集 · 同步控制 · 实时分析", style="SubTitle.TLabel").pack(anchor="w", pady=(1, 0))
        ttk.Label(header, textvariable=self.device_summary, style="Badge.TLabel").pack(side="right", pady=7)

        workspace = ttk.Frame(self, style="Workspace.TFrame")
        workspace.grid(row=1, column=0, sticky="nsew", padx=12)
        workspace.columnconfigure(0, minsize=292)
        workspace.columnconfigure(1, weight=1, minsize=500)
        workspace.columnconfigure(2, minsize=292)
        workspace.rowconfigure(0, weight=1)

        left = ttk.Frame(workspace, style="Panel.TFrame", padding=13)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        center = ttk.Frame(workspace, style="Panel.TFrame", padding=10)
        center.grid(row=0, column=1, sticky="nsew", padx=6)
        right = ttk.Frame(workspace, style="Panel.TFrame", padding=13)
        right.grid(row=0, column=2, sticky="nsew", padx=(6, 0))
        center.columnconfigure(0, weight=1)
        center.rowconfigure(2, weight=1)

        self._build_connection(left)
        self._build_parameters(left)
        self._build_plot(center)
        self._build_acquisition(right)
        detail_tabs = ttk.Notebook(right)
        detail_tabs.pack(fill="both", expand=True)
        channel_tab = ttk.Frame(detail_tabs, style="Panel.TFrame", padding=(0, 7, 0, 0))
        history_tab = ttk.Frame(detail_tabs, style="Panel.TFrame", padding=(0, 7, 0, 0))
        save_tab = ttk.Frame(detail_tabs, style="Panel.TFrame", padding=(0, 7, 0, 0))
        detail_tabs.add(channel_tab, text="通道状态")
        detail_tabs.add(history_tab, text="最近记录")
        detail_tabs.add(save_tab, text="数据保存")
        self._build_devices(channel_tab)
        self._build_history(history_tab)
        self._build_data_save(save_tab)

        statusbar = ttk.Frame(self, style="Panel.TFrame")
        statusbar.grid(row=2, column=0, sticky="ew", padx=12, pady=(7, 10))
        self.status_dot = tk.Label(statusbar, text="●", bg="#0a1014", fg="#60717a", padx=12, pady=7, font=("Segoe UI", 10, "bold"))
        self.status_dot.pack(side="left")
        ttk.Label(statusbar, textvariable=self.status, style="Status.TLabel").pack(side="left", fill="x", expand=True)
        ttk.Label(statusbar, text="F5 单次采集  ·  Esc 停止  ·  Ctrl+S 保存", style="Status.TLabel").pack(side="right")

    def _build_connection(self, parent) -> None:
        ttk.Label(parent, text="DEVICE", style="Eyebrow.TLabel").pack(anchor="w")
        ttk.Label(parent, text="设备连接", style="Section.TLabel").pack(anchor="w", pady=(1, 2))
        ttk.Label(parent, text="选择通信方式并连接 Maria 的四个光谱通道。", style="Hint.TLabel", wraplength=255).pack(anchor="w", pady=(0, 10))
        box = ttk.LabelFrame(parent, text=" 连接设置 ", padding=9)
        box.pack(fill="x", pady=(0, 10))
        box.columnconfigure(1, weight=1)
        ttk.Label(box, text="通信方式").grid(row=0, column=0, sticky="w", pady=3)
        mode = ttk.Combobox(box, textvariable=self.v["connection"], values=("USB", "网络"), state="readonly", width=16)
        mode.grid(row=0, column=1, sticky="ew", pady=3)
        mode.bind("<<ComboboxSelected>>", lambda _: self._update_network_state())
        ttk.Checkbutton(box, text="使用仿真设备（推荐先试运行）", variable=self.v["simulation"]).grid(row=1, column=0, columnspan=2, sticky="w", pady=(4, 6))
        ttk.Label(box, text="SDK 文件").grid(row=2, column=0, sticky="w", pady=3)
        ttk.Entry(box, textvariable=self.v["dll"], width=22).grid(row=2, column=1, sticky="ew", pady=3)
        self.network_label = ttk.Label(box, text="网络设备")
        self.network_label.grid(row=3, column=0, sticky="w", pady=3)
        self.network_entry = ttk.Entry(box, textvariable=self.v["network_devices"], width=22)
        self.network_entry.grid(row=3, column=1, sticky="ew", pady=3)
        self.network_hint = ttk.Label(box, text="格式：型号@IP，端口 8888", style="Hint.TLabel")
        self.network_hint.grid(row=4, column=0, columnspan=2, sticky="w")
        row = ttk.Frame(box, style="Panel.TFrame")
        row.grid(row=5, column=0, columnspan=2, sticky="ew", pady=(7, 0))
        row.columnconfigure((0, 1), weight=1)
        self.scan_button = ttk.Button(row, text="扫描网络", command=self.scan_network)
        self.scan_button.grid(row=0, column=0, sticky="ew", padx=(0, 3))
        ttk.Label(row, text="PC 地址建议 24.5.10.100/8", style="Hint.TLabel").grid(row=0, column=1, sticky="w", padx=(7, 0))
        buttons = ttk.Frame(box, style="Panel.TFrame")
        buttons.grid(row=6, column=0, columnspan=2, sticky="ew", pady=(9, 0))
        buttons.columnconfigure((0, 1), weight=1)
        self.connect_button = ttk.Button(buttons, text="连接设备", command=self.connect, style="Accent.TButton")
        self.connect_button.grid(row=0, column=0, sticky="ew", padx=(0, 3))
        self.disconnect_button = ttk.Button(buttons, text="断开连接", command=self.disconnect, state="disabled")
        self.disconnect_button.grid(row=0, column=1, sticky="ew", padx=(3, 0))
        self.network_widgets = (self.network_label, self.network_entry, self.network_hint, row)
        self._update_network_state()

    def _build_devices(self, parent) -> None:
        box = ttk.LabelFrame(parent, text=" 通道状态 ", padding=7)
        box.pack(fill="x", pady=(0, 9))
        self.device_tree = ttk.Treeview(box, columns=("ch", "range", "pixels"), show="headings", height=4)
        self.device_tree.heading("ch", text="通道 / 序列号")
        self.device_tree.heading("range", text="波段 nm")
        self.device_tree.heading("pixels", text="像素")
        self.device_tree.column("ch", width=112, anchor="w")
        self.device_tree.column("range", width=88, anchor="center")
        self.device_tree.column("pixels", width=45, anchor="e")
        self.device_tree.pack(fill="x")
        for channel in range(1, 5):
            self.device_tree.insert("", "end", iid=f"ch{channel}", values=(f"CH{channel}  未连接", "—", "—"))

    def _build_parameters(self, parent) -> None:
        ttk.Label(parent, text="ACQUISITION", style="Eyebrow.TLabel").pack(anchor="w", pady=(1, 0))
        ttk.Label(parent, text="采集参数", style="Section.TLabel").pack(anchor="w", pady=(1, 2))
        ttk.Label(parent, text="参数会统一写入当前已连接的全部通道。", style="Hint.TLabel", wraplength=255).pack(anchor="w", pady=(0, 9))
        box = ttk.LabelFrame(parent, text=" 曝光与触发 ", padding=9)
        box.pack(fill="x")
        box.columnconfigure(1, weight=1)
        self._entry(box, "积分时间 (μs)", "integration_us", 0)
        self._entry(box, "硬件平均次数", "averages", 1)
        self._entry(box, "硬件平滑 (0/奇数)", "hardware_boxcar", 2)
        self._entry(box, "软件平滑 (0/奇数)", "software_boxcar", 3)
        self._entry(box, "采集延迟 (μs)", "delay_us", 4)
        ttk.Label(box, text="触发模式").grid(row=5, column=0, sticky="w", pady=2)
        trigger = ttk.Combobox(box, textvariable=self.v["trigger"], values=tuple(TRIGGER_MODES), state="readonly", width=17)
        trigger.grid(row=5, column=1, sticky="ew", pady=2)
        trigger.bind("<<ComboboxSelected>>", lambda _: self._trigger_hint())
        self.trigger_hint = ttk.Label(box, text="软件命令启动采集", style="Hint.TLabel", wraplength=250)
        self.trigger_hint.grid(row=6, column=0, columnspan=2, sticky="w", pady=(1, 5))
        self.apply_button = ttk.Button(box, text="应用参数到全部通道", command=self.apply_parameters, state="disabled")
        self.apply_button.grid(row=7, column=0, columnspan=2, sticky="ew")

    def _build_plot(self, parent) -> None:
        top = ttk.Frame(parent, style="Panel.TFrame")
        top.grid(row=0, column=0, sticky="ew", padx=3, pady=(1, 8))
        title = ttk.Frame(top, style="Panel.TFrame")
        title.pack(side="left")
        ttk.Label(title, text="LIVE SPECTRUM", style="Eyebrow.TLabel").pack(anchor="w")
        ttk.Label(title, text="实时光谱监视器", style="Section.TLabel").pack(anchor="w")
        ttk.Label(top, textvariable=self.frame_summary, style="Badge.TLabel").pack(side="right", pady=5)

        metrics = ttk.Frame(parent, style="Panel.TFrame")
        metrics.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        for column in range(4):
            metrics.columnconfigure(column, weight=1, uniform="metric")
        self._metric_card(metrics, 0, "主峰波长", self.metric_peak_wavelength, "#2dd4ff")
        self._metric_card(metrics, 1, "峰值强度", self.metric_peak_intensity, "#f5c451")
        self._metric_card(metrics, 2, "饱和度", self.metric_saturation, "#2fe3a0")
        self._metric_card(metrics, 3, "采集速率", self.metric_rate, "#c986ff")

        self.plot = SpectrumCanvas(parent)
        self.plot.grid(row=2, column=0, sticky="nsew", padx=2)

        plot_footer = ttk.Frame(parent, style="Panel.TFrame")
        plot_footer.grid(row=3, column=0, sticky="ew", padx=3, pady=(6, 0))
        ttk.Label(plot_footer, text="滚轮缩放 · 移动鼠标读取坐标 · 右键恢复全谱", style="Hint.TLabel").pack(side="left")
        plot_actions = ttk.Frame(plot_footer, style="Panel.TFrame")
        plot_actions.pack(side="right")
        self.clear_plot_button = ttk.Button(
            plot_actions,
            text="清除显示",
            command=self.clear_plot_display,
            state="disabled",
        )
        self.clear_plot_button.pack(side="left")
        self.quick_save_button = ttk.Button(
            plot_actions,
            text="手动保存",
            command=self.save_current,
            state="disabled",
        )
        self.quick_save_button.pack(side="left", padx=(5, 0))
        self.plot_settings_button = ttk.Button(plot_actions, text="⚙  显示与拼接设置", command=self._toggle_plot_settings)
        self.plot_settings_button.pack(side="left", padx=(5, 0))

        self.plot_settings_panel = ttk.Frame(parent, style="Raised.TFrame", padding=(11, 9))
        self.plot_settings_panel.grid(row=4, column=0, sticky="ew", pady=(6, 0))
        self.plot_settings_panel.columnconfigure(0, weight=1)
        settings_header = ttk.Frame(self.plot_settings_panel, style="Raised.TFrame")
        settings_header.grid(row=0, column=0, sticky="ew", pady=(0, 7))
        ttk.Label(settings_header, text="显示与拼接设置", background="#121b22", foreground="#e5eef2", font=("Microsoft YaHei UI", 10, "bold")).pack(side="left")
        ttk.Label(settings_header, text="仅影响图表显示和拼接结果", style="RaisedHint.TLabel").pack(side="right")

        tools = ttk.Frame(self.plot_settings_panel, style="Raised.TFrame")
        tools.grid(row=1, column=0, sticky="ew", pady=(0, 7))
        tools.columnconfigure(1, weight=1)
        ttk.Label(tools, text="视图", background="#121b22", foreground="#9db0b9").grid(row=0, column=0, sticky="w", padx=(0, 6))
        display = ttk.Combobox(tools, textvariable=self.v["display"], values=("四通道叠加 + 拼接", "仅拼接光谱", "仅四通道", "仅当前通道"), state="readonly", width=18)
        display.grid(row=0, column=1, sticky="w", padx=(0, 7))
        channel = ttk.Combobox(tools, textvariable=self.v["channel"], values=tuple(f"通道 {i}" for i in range(1, 5)), state="readonly", width=8)
        channel.grid(row=0, column=2, sticky="w", padx=(0, 8))
        peak_check = tk.Checkbutton(
            tools,
            text="标注主峰",
            variable=self.v["show_peaks"],
            command=self._update_plot_options,
            bg="#121b22",
            fg="#c9d6db",
            activebackground="#121b22",
            activeforeground="#ffffff",
            selectcolor="#16262e",
            highlightthickness=0,
            font=("Microsoft YaHei UI", 9),
        )
        peak_check.grid(row=0, column=3, sticky="w")
        ttk.Button(tools, text="恢复全谱", command=self.plot.reset_view).grid(row=0, column=4, sticky="e", padx=(8, 0))
        display.bind("<<ComboboxSelected>>", lambda _: self._update_plot_options())
        channel.bind("<<ComboboxSelected>>", lambda _: self._update_plot_options())

        ttk.Separator(self.plot_settings_panel, orient="horizontal").grid(row=2, column=0, sticky="ew", pady=(0, 7))
        stitch_tools = ttk.Frame(self.plot_settings_panel, style="Raised.TFrame")
        stitch_tools.grid(row=3, column=0, sticky="ew")
        stitch_tools.columnconfigure(1, weight=1)
        ttk.Label(stitch_tools, text="拼接", background="#121b22", foreground="#9db0b9").grid(row=0, column=0, sticky="w", padx=(0, 6))
        stitch_mode = ttk.Combobox(
            stitch_tools,
            textvariable=self.v["stitch_mode"],
            values=tuple(STITCH_MODES),
            state="readonly",
            width=17,
        )
        stitch_mode.grid(row=0, column=1, sticky="w", padx=(0, 9))
        ttk.Label(stitch_tools, text="CH1–CH4 系数", background="#121b22", foreground="#9db0b9").grid(row=0, column=2, sticky="w")
        self.calibration_entry = ttk.Entry(stitch_tools, textvariable=self.v["calibration_factors"], width=18)
        self.calibration_entry.grid(row=0, column=3, sticky="ew", padx=(6, 0))
        stitch_tools.columnconfigure(3, weight=1)
        ttk.Label(stitch_tools, textvariable=self.stitch_hint, style="RaisedHint.TLabel", wraplength=590).grid(row=1, column=0, columnspan=4, sticky="w", pady=(6, 0))
        stitch_mode.bind("<<ComboboxSelected>>", lambda _: self._stitch_mode_changed())
        self.calibration_entry.bind("<Return>", lambda _: self._reprocess_current())
        self.calibration_entry.bind("<FocusOut>", lambda _: self._reprocess_current(show_errors=False))
        self._update_calibration_entry_state()
        self.plot_settings_panel.grid_remove()

    def _toggle_plot_settings(self) -> None:
        self.plot_settings_visible = not self.plot_settings_visible
        if self.plot_settings_visible:
            self.plot_settings_panel.grid()
            self.plot_settings_button.configure(text="收起设置", style="Accent.TButton")
        else:
            self.plot_settings_panel.grid_remove()
            self.plot_settings_button.configure(text="⚙  显示与拼接设置", style="TButton")

    def _metric_card(self, parent, column: int, name: str, variable: tk.StringVar, accent: str) -> None:
        card = ttk.Frame(parent, style="MetricCard.TFrame", padding=(10, 8))
        card.grid(row=0, column=column, sticky="ew", padx=(0 if column == 0 else 3, 0 if column == 3 else 3))
        tk.Frame(card, bg=accent, width=3, height=32).pack(side="left", fill="y", padx=(0, 8))
        text = ttk.Frame(card, style="MetricCard.TFrame")
        text.pack(side="left", fill="x", expand=True)
        ttk.Label(text, text=name, style="MetricName.TLabel").pack(anchor="w")
        ttk.Label(text, textvariable=variable, style="MetricValue.TLabel").pack(anchor="w", pady=(2, 0))

    def _build_acquisition(self, parent) -> None:
        ttk.Label(parent, text="MEASUREMENT", style="Eyebrow.TLabel").pack(anchor="w")
        ttk.Label(parent, text="采集控制", style="Section.TLabel").pack(anchor="w", pady=(1, 2))
        ttk.Label(parent, text="先设置参数，再选择单次、定次或连续采集。", style="Hint.TLabel", wraplength=260).pack(anchor="w", pady=(0, 9))
        box = ttk.LabelFrame(parent, text=" 测量任务 ", padding=9)
        box.pack(fill="x", pady=(0, 9))
        box.columnconfigure(1, weight=1)
        self._entry(box, "定次采集帧数", "frame_count", 0)
        self._entry(box, "帧间隔 (ms)", "interval_ms", 1)
        self.single_button = ttk.Button(box, text="●  单次采集", command=lambda: self.start_capture(1), style="Success.TButton", state="disabled")
        self.single_button.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(7, 0))
        self.series_button = ttk.Button(box, text="定次", command=self.start_series, state="disabled")
        self.series_button.grid(row=3, column=0, sticky="ew", padx=(0, 3), pady=(4, 0))
        self.continuous_button = ttk.Button(box, text="连续", command=lambda: self.start_capture(None), state="disabled")
        self.continuous_button.grid(row=3, column=1, sticky="ew", padx=(3, 0), pady=(4, 0))
        self.stop_button = ttk.Button(box, text="■  停止 / 取消触发等待", command=self.stop_capture, style="Danger.TButton", state="disabled")
        self.stop_button.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(4, 0))
        self.dark_button = ttk.Button(box, text="采集暗谱", command=self.capture_dark, state="disabled")
        self.dark_button.grid(row=5, column=0, sticky="ew", padx=(0, 3), pady=(4, 0))
        ttk.Checkbutton(box, text="扣除暗谱", variable=self.v["subtract_dark"], command=self._reprocess_current).grid(row=5, column=1, sticky="w", padx=(5, 0), pady=(4, 0))
        ttk.Progressbar(box, variable=self.progress, maximum=100, style="Tech.Horizontal.TProgressbar").grid(row=6, column=0, columnspan=2, sticky="ew", pady=(8, 0))

    def _build_data_save(self, parent) -> None:
        save = ttk.LabelFrame(parent, text=" 数据保存 ", padding=10)
        save.pack(fill="both", expand=True)
        save.columnconfigure(1, weight=1)
        ttk.Checkbutton(save, text="采集时自动保存 CSV", variable=self.v["auto_save"]).grid(row=0, column=0, columnspan=2, sticky="w")
        ttk.Label(save, text="保存目录").grid(row=1, column=0, sticky="w", pady=(4, 2))
        ttk.Entry(save, textvariable=self.v["save_dir"], width=18).grid(row=1, column=1, sticky="ew", pady=(4, 2))
        ttk.Button(save, text="选择目录", command=self.choose_save_dir).grid(row=2, column=0, sticky="ew", padx=(0, 3), pady=(3, 0))
        self.save_button = ttk.Button(save, text="手动保存当前帧", command=self.save_current, state="disabled")
        self.save_button.grid(row=2, column=1, sticky="ew", padx=(3, 0), pady=(3, 0))
        ttk.Label(
            save,
            text="CSV 同时保存四个独立通道、拼接光谱和本次采集参数。",
            style="Hint.TLabel",
            wraplength=245,
        ).grid(row=3, column=0, columnspan=2, sticky="w", pady=(9, 0))

    def _build_history(self, parent) -> None:
        box = ttk.LabelFrame(parent, text=" 最近记录 ", padding=7)
        box.pack(fill="both", expand=True)
        self.history_tree = ttk.Treeview(box, columns=("seq", "time", "peak"), show="headings", height=4)
        self.history_tree.heading("seq", text="帧")
        self.history_tree.heading("time", text="时间")
        self.history_tree.heading("peak", text="主峰 nm")
        self.history_tree.column("seq", width=40, anchor="center")
        self.history_tree.column("time", width=75, anchor="center")
        self.history_tree.column("peak", width=75, anchor="e")
        self.history_tree.pack(fill="both", expand=True)
        self.history_tree.bind("<<TreeviewSelect>>", self._load_history)
        ttk.Label(box, text="选择记录可重新查看 · 保留最近 50 帧", style="Hint.TLabel").pack(anchor="w", pady=(5, 0))

    def _entry(self, parent, label: str, key: str, row: int) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=(0, 7), pady=3)
        ttk.Entry(parent, textvariable=self.v[key], width=14).grid(row=row, column=1, sticky="ew", pady=3)

    def _update_network_state(self) -> None:
        network = self.v["connection"].get() == "网络"
        if network:
            for widget in self.network_widgets:
                widget.grid()
            self.network_entry.configure(state="normal")
            self.scan_button.configure(state="disabled" if self.controller else "normal")
        else:
            for widget in self.network_widgets:
                widget.grid_remove()
            self.scan_button.configure(state="disabled")

    def _trigger_hint(self) -> None:
        mode = TRIGGER_MODES[self.v["trigger"].get()]
        text = {
            0: "软件命令启动采集",
            2: "等待 3.3 V 上升沿；停止按钮会调用 SDK 取消等待",
            6: "1~10 ns 级响应；电子底噪可能升高",
        }[mode]
        self.trigger_hint.configure(text=text)

    def _parse_network_devices(self) -> list[NetworkDevice]:
        port = int(self.v["port"].get())
        result: list[NetworkDevice] = []
        for token in self.v["network_devices"].get().replace("；", ",").replace(";", ",").split(","):
            token = token.strip()
            if not token:
                continue
            if "@" not in token:
                raise ValueError(f"网络设备“{token}”格式错误，应为 型号@IP")
            model, ip = (part.strip() for part in token.split("@", 1))
            if not model or not ip:
                raise ValueError("网络设备的型号和 IP 不能为空")
            result.append(NetworkDevice(model, ip, port))
        if not result:
            raise ValueError("请填写至少一个网络设备，或先执行扫描网络")
        return result

    def scan_network(self) -> None:
        if self.running:
            return
        self.status.set("正在监听光谱仪 UDP 广播（约 2 秒）…")
        self.scan_button.configure(state="disabled")

        def worker():
            try:
                found = discover_network_devices()
                self.events.put(("scan", found))
            except Exception as exc:
                self.events.put(("error", f"网络扫描失败：{exc}"))

        threading.Thread(target=worker, daemon=True).start()

    def connect(self) -> None:
        if self.running:
            return
        try:
            simulation = bool(self.v["simulation"].get())
            connection = self.v["connection"].get()
            network = self._parse_network_devices() if connection == "网络" else []
            dll = self.v["dll"].get()
        except ValueError as exc:
            messagebox.showerror("连接参数错误", str(exc))
            return
        self.status.set("正在连接并读取四通道信息…")
        self.connect_button.configure(state="disabled")

        def worker():
            controller = None
            try:
                controller = SimulationController() if simulation else SeaSDKController(dll)
                devices = controller.connect_network(network) if connection == "网络" else controller.connect_usb(4)
                self.events.put(("connected", controller, devices))
            except Exception as exc:
                if controller:
                    controller.disconnect()
                self.events.put(("error", f"连接失败：{exc}"))

        threading.Thread(target=worker, daemon=True).start()

    def disconnect(self) -> None:
        self.stop_capture()
        controller, self.controller = self.controller, None
        if controller:
            threading.Thread(target=controller.disconnect, daemon=True).start()
        self.dark_spectra.clear()
        self._set_connected_state(False)
        self.device_summary.set("0 / 4 CHANNELS ONLINE")
        self.metric_rate.set("离线")
        for channel in range(1, 5):
            self.device_tree.item(f"ch{channel}", values=(f"CH{channel}  未连接", "—", "—"))
        self.status.set("已断开光谱仪")

    def _set_connected_state(self, connected: bool) -> None:
        self.connect_button.configure(state="disabled" if connected else "normal")
        self.disconnect_button.configure(state="normal" if connected else "disabled")
        self.scan_button.configure(state="disabled" if connected or self.v["connection"].get() != "网络" else "normal")
        self.status_dot.configure(fg="#2fe3a0" if connected else "#60717a")
        for button in (self.apply_button, self.single_button, self.series_button, self.continuous_button, self.dark_button):
            button.configure(state="normal" if connected else "disabled")

    def _read_settings(self) -> dict[str, object]:
        integration = int(self.v["integration_us"].get())
        averages = int(self.v["averages"].get())
        hardware_boxcar = int(self.v["hardware_boxcar"].get())
        software_boxcar = int(self.v["software_boxcar"].get())
        delay = float(self.v["delay_us"].get())
        interval = int(self.v["interval_ms"].get())
        if integration < 1 or averages < 1 or delay < 0 or interval < 0:
            raise ValueError("积分时间、平均次数、采集延迟和帧间隔不能为负数，平均次数至少为 1")
        for name, value in (("硬件平滑", hardware_boxcar), ("软件平滑", software_boxcar)):
            if value < 0 or (value > 0 and value % 2 == 0):
                raise ValueError(f"{name}必须为 0 或正奇数")
        stitch_mode, calibration_factors = self._read_stitch_options()
        return {
            "integration_us": integration,
            "averages": averages,
            "hardware_boxcar": hardware_boxcar,
            "software_boxcar": software_boxcar,
            "delay_us": delay,
            "trigger_mode": TRIGGER_MODES[self.v["trigger"].get()],
            "interval_ms": interval,
            "stitch_mode": stitch_mode,
            "calibration_factors": calibration_factors,
        }

    def apply_parameters(self) -> None:
        if not self.controller or self.running:
            return
        try:
            settings = self._read_settings()
        except ValueError as exc:
            messagebox.showerror("采集参数错误", str(exc))
            return
        self.status.set("正在向全部通道写入参数…")
        controller = self.controller

        def worker():
            try:
                controller.configure(
                    settings["integration_us"], settings["averages"], settings["hardware_boxcar"],
                    settings["trigger_mode"], settings["delay_us"],
                )
                self.events.put(("parameters", settings))
            except Exception as exc:
                self.events.put(("error", f"应用参数失败：{exc}"))

        threading.Thread(target=worker, daemon=True).start()

    def start_series(self) -> None:
        try:
            count = int(self.v["frame_count"].get())
            if count < 1:
                raise ValueError
        except ValueError:
            messagebox.showerror("采集参数错误", "定次采集帧数必须为正整数")
            return
        self.start_capture(count)

    def capture_dark(self) -> None:
        if messagebox.askokcancel("采集暗谱", "请确认光路已遮光、激光器已关闭。\n\n将按当前参数采集一帧，并保存为四通道暗谱。"):
            self._capture_dark = True
            self.start_capture(1)

    def start_capture(self, total: int | None) -> None:
        if not self.controller or self.running:
            return
        try:
            settings = self._read_settings()
            save_dir = Path(self.v["save_dir"].get()).expanduser()
            auto_save = bool(self.v["auto_save"].get())
            subtract_dark_enabled = bool(self.v["subtract_dark"].get())
        except (ValueError, OSError) as exc:
            self._capture_dark = False
            messagebox.showerror("采集参数错误", str(exc))
            return
        self.running = True
        controller = self.controller
        self.status_dot.configure(fg="#2dd4ff")
        self.stop_event.clear()
        self.progress.set(0)
        self.stop_button.configure(state="normal")
        for button in (self.single_button, self.series_button, self.continuous_button, self.dark_button, self.apply_button):
            button.configure(state="disabled")
        mode = self.v["trigger"].get()
        dark_capture_requested = self._capture_dark
        self.status.set(f"采集中 · {mode}" + (" · 等待外部触发" if settings["trigger_mode"] in (2, 6) else ""))

        def worker():
            started = time.perf_counter()
            acquired = 0
            try:
                controller.configure(
                    settings["integration_us"], settings["averages"], settings["hardware_boxcar"],
                    settings["trigger_mode"], settings["delay_us"],
                )
                while not self.stop_event.is_set() and (total is None or acquired < total):
                    raw = controller.acquire()
                    if self.stop_event.is_set():
                        break
                    acquired += 1
                    self.sequence += 1
                    frame = build_processed_frame(
                        raw,
                        self.dark_spectra,
                        subtract_dark_enabled and bool(self.dark_spectra),
                        int(settings["software_boxcar"]),
                        self.sequence,
                        {
                            "integration_us": settings["integration_us"],
                            "averages": settings["averages"],
                            "hardware_boxcar": settings["hardware_boxcar"],
                            "software_boxcar": settings["software_boxcar"],
                            "delay_us": settings["delay_us"],
                            "trigger_mode": settings["trigger_mode"],
                            "stitch_mode": settings["stitch_mode"],
                            "calibration_factors": ",".join(f"{value:g}" for value in settings["calibration_factors"]),
                        },
                        stitch_mode=settings["stitch_mode"],
                        calibration_factors={i + 1: value for i, value in enumerate(settings["calibration_factors"])},
                    )
                    if dark_capture_requested:
                        self.dark_spectra = {s.channel: s for s in raw}
                        frame = build_processed_frame(
                            raw,
                            self.dark_spectra,
                            False,
                            0,
                            self.sequence,
                            frame.metadata,
                            settings["stitch_mode"],
                            {i + 1: value for i, value in enumerate(settings["calibration_factors"])},
                        )
                    if auto_save:
                        stamp = frame.captured_at.strftime("%Y%m%d_%H%M%S_%f")[:-3]
                        save_frame_csv(frame, save_dir / f"Maria_{stamp}_F{frame.sequence:05d}.csv")
                    elapsed = time.perf_counter() - started
                    self.events.put(("frame", frame, acquired, total, elapsed, dark_capture_requested))
                    if total is None or acquired < total:
                        self.stop_event.wait(settings["interval_ms"] / 1000.0)
                self.events.put(("capture_done", acquired, time.perf_counter() - started, self.stop_event.is_set()))
            except Exception as exc:
                cancelled = self.stop_event.is_set() and "取消" in str(exc)
                self.events.put(("capture_done", acquired, time.perf_counter() - started, cancelled, None if cancelled else str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def stop_capture(self) -> None:
        if not self.running:
            return
        self.stop_event.set()
        self.status.set("正在停止采集并取消外触发等待…")
        if self.controller:
            threading.Thread(target=self.controller.cancel_external_trigger, daemon=True).start()

    def _capture_finished(self, acquired: int, elapsed: float, stopped: bool, error: str | None = None) -> None:
        self.running = False
        self._capture_dark = False
        self.stop_button.configure(state="disabled")
        if self.controller:
            for button in (self.single_button, self.series_button, self.continuous_button, self.dark_button, self.apply_button):
                button.configure(state="normal")
        if error:
            self.status_dot.configure(fg="#ef657e")
            self.status.set(f"采集失败：{error}")
            messagebox.showerror("采集失败", error)
        else:
            self.status_dot.configure(fg="#2fe3a0" if self.controller else "#60717a")
            state = "已停止" if stopped else "采集完成"
            self.status.set(f"{state} · {acquired} 帧 · {elapsed:.2f} s")

    def _on_frame(self, frame: Frame, acquired: int, total: int | None, elapsed: float, dark_captured: bool) -> None:
        self.current_frame = frame
        self.history.append(frame)
        if len(self.history) > 50:
            removed = self.history.pop(0)
            if self.history_tree.exists(str(removed.sequence)):
                self.history_tree.delete(str(removed.sequence))
        peak_wl, _ = self._peak(frame)
        rate = acquired / elapsed if elapsed > 0 else 0
        self._update_frame_metrics(frame, rate)
        self.history_tree.insert("", 0, iid=str(frame.sequence), values=(frame.sequence, frame.captured_at.strftime("%H:%M:%S"), f"{peak_wl:.2f}"))
        self.plot.set_frame(frame)
        self.save_button.configure(state="normal")
        self.quick_save_button.configure(state="normal")
        self.clear_plot_button.configure(state="normal")
        self.progress.set((acquired / total * 100) if total else (acquired % 100))
        if dark_captured:
            self.status.set(f"暗谱已保存 · {len(self.dark_spectra)} 通道；后续可启用扣除暗谱")
        else:
            self.status.set(f"采集中 · 已完成 {acquired}" + (f" / {total}" if total else "") + f" 帧 · {rate:.2f} fps")

    @staticmethod
    def _peak(frame: Frame) -> tuple[float, float]:
        spectrum = frame.stitched or (frame.displayed[0] if frame.displayed else None)
        if not spectrum or not spectrum.intensities:
            return 0.0, 0.0
        index = max(range(len(spectrum.intensities)), key=spectrum.intensities.__getitem__)
        return spectrum.wavelengths[index], spectrum.intensities[index]

    def _update_frame_metrics(self, frame: Frame, rate: float | None = None) -> None:
        peak_wl, peak_value = self._peak(frame)
        maximum = max((d.maximum_intensity for d in self.controller.devices), default=65535) if self.controller else 65535
        saturation = peak_value / maximum * 100 if maximum else 0.0
        self.frame_summary.set(f"FRAME {frame.sequence:04d}")
        self.metric_peak_wavelength.set(f"{peak_wl:.2f} nm")
        self.metric_peak_intensity.set(f"{peak_value:,.0f} count")
        self.metric_saturation.set(f"{saturation:.1f} %")
        if rate is not None:
            self.metric_rate.set(f"{rate:.2f} fps")

    def _update_plot_options(self) -> None:
        channel = int(self.v["channel"].get().split()[-1])
        self.plot.set_options(self.v["display"].get(), channel, bool(self.v["show_peaks"].get()))

    def _read_stitch_options(self) -> tuple[str, tuple[float, float, float, float]]:
        label = self.v["stitch_mode"].get()
        if label not in STITCH_MODES:
            raise ValueError("请选择有效的拼接方式")
        mode = STITCH_MODES[label]
        if mode != "calibrated":
            return mode, (1.0, 1.0, 1.0, 1.0)
        text = self.v["calibration_factors"].get().replace("，", ",").replace("；", ",").replace(";", ",")
        parts = [part.strip() for part in text.split(",") if part.strip()]
        if len(parts) != 4:
            raise ValueError("标定系数应填写 CH1～CH4 共 4 个数值，例如 1.0, 1.0, 1.0, 1.0")
        try:
            values = tuple(float(part) for part in parts)
        except ValueError as exc:
            raise ValueError("标定系数包含无效数字") from exc
        if any(not math.isfinite(value) or value <= 0 for value in values):
            raise ValueError("四个通道的标定系数都必须是大于 0 的有限数值")
        return mode, values

    def _update_calibration_entry_state(self) -> None:
        enabled = self.v["stitch_mode"].get() == "标定系数拼接"
        self.calibration_entry.configure(state="normal" if enabled else "disabled")
        hints = {
            "原始强度直接拼接": "不缩放强度，在重叠区中点切换通道；适合核对原始计数。",
            "重叠区自动校正": "利用相邻通道重叠区自动对齐强度，适合快速观察完整谱线。",
            "标定系数拼接": "按 CH1–CH4 响应系数缩放；正式系数应由标准光源标定获得。",
        }
        self.stitch_hint.set(hints.get(self.v["stitch_mode"].get(), ""))

    def _stitch_mode_changed(self) -> None:
        self._update_calibration_entry_state()
        self._reprocess_current()

    def _reprocess_current(self, show_errors: bool = True) -> None:
        if not self.current_frame:
            return
        try:
            software_boxcar = int(self.v["software_boxcar"].get())
            stitch_mode, calibration_factors = self._read_stitch_options()
        except ValueError as exc:
            if show_errors:
                messagebox.showerror("拼接参数错误", str(exc))
            return
        metadata = dict(self.current_frame.metadata)
        metadata["stitch_mode"] = stitch_mode
        metadata["calibration_factors"] = ",".join(f"{value:g}" for value in calibration_factors)
        self.current_frame = build_processed_frame(
            self.current_frame.raw,
            self.dark_spectra,
            bool(self.v["subtract_dark"].get()) and bool(self.dark_spectra),
            software_boxcar,
            self.current_frame.sequence,
            metadata,
            stitch_mode,
            {i + 1: value for i, value in enumerate(calibration_factors)},
        )
        self.plot.set_frame(self.current_frame)
        self._update_frame_metrics(self.current_frame)
        self.save_button.configure(state="normal")
        self.quick_save_button.configure(state="normal")
        self.clear_plot_button.configure(state="normal")
        self.status.set(f"已按“{self.v['stitch_mode'].get()}”刷新当前帧")

    def _load_history(self, _event=None) -> None:
        selected = self.history_tree.selection()
        if not selected:
            return
        sequence = int(selected[0])
        frame = next((item for item in self.history if item.sequence == sequence), None)
        if frame:
            self.current_frame = frame
            self.plot.set_frame(frame)
            self._update_frame_metrics(frame)
            self.metric_rate.set("历史帧")
            self.save_button.configure(state="normal")
            self.quick_save_button.configure(state="normal")
            self.clear_plot_button.configure(state="normal")

    def clear_plot_display(self) -> None:
        """Clear only the visible plot while retaining data for saving or recall."""
        self.plot.set_frame(None)
        self.frame_summary.set("DISPLAY CLEARED")
        self.metric_peak_wavelength.set("—")
        self.metric_peak_intensity.set("—")
        self.metric_saturation.set("—")
        self.metric_rate.set("—")
        self.clear_plot_button.configure(state="disabled")
        if self.current_frame:
            self.save_button.configure(state="normal")
            self.quick_save_button.configure(state="normal")
        self.status.set("光谱显示已清空；当前帧和历史记录仍保留，可继续手动保存")

    def choose_save_dir(self) -> None:
        folder = filedialog.askdirectory(initialdir=self.v["save_dir"].get() or str(Path.cwd()))
        if folder:
            self.v["save_dir"].set(folder)

    def save_current(self) -> None:
        if not self.current_frame:
            return
        stamp = self.current_frame.captured_at.strftime("%Y%m%d_%H%M%S_%f")[:-3]
        target = filedialog.asksaveasfilename(
            title="保存四通道光谱",
            initialdir=self.v["save_dir"].get(),
            initialfile=f"Maria_{stamp}_F{self.current_frame.sequence:05d}.csv",
            defaultextension=".csv",
            filetypes=(("CSV 光谱数据", "*.csv"), ("所有文件", "*.*")),
        )
        if target:
            try:
                save_frame_csv(self.current_frame, target)
                self.status.set(f"已手动保存：{target}")
            except OSError as exc:
                messagebox.showerror("保存失败", str(exc))

    def _poll_events(self) -> None:
        try:
            while True:
                event = self.events.get_nowait()
                kind = event[0]
                if kind == "connected":
                    _, self.controller, devices = event
                    self._set_connected_state(True)
                    for channel in range(1, 5):
                        if channel <= len(devices):
                            d = devices[channel - 1]
                            self.device_tree.item(f"ch{channel}", values=(f"CH{channel}  {d.serial}", f"{d.wavelength_min:.0f}–{d.wavelength_max:.0f}", d.pixels))
                        else:
                            self.device_tree.item(f"ch{channel}", values=(f"CH{channel}  未发现", "—", "—"))
                    self.device_summary.set(f"{len(devices)} / 4 CHANNELS ONLINE")
                    qualifier = "仿真设备" if isinstance(self.controller, SimulationController) else "实机"
                    self.status.set(f"连接成功 · {qualifier} · 识别 {len(devices)} 个通道")
                    if len(devices) != 4:
                        messagebox.showwarning("通道数量异常", f"Maria 应有 4 个通道，当前仅识别 {len(devices)} 个。可单通道采集，但请检查连接。")
                elif kind == "scan":
                    found = event[1]
                    self.scan_button.configure(state="normal")
                    if found:
                        self.v["network_devices"].set(", ".join(f"{d.model}@{d.ip}" for d in found))
                        self.status.set(f"网络扫描完成 · 发现 {len(found)} 台光谱仪")
                    else:
                        self.status.set("网络扫描完成 · 未收到光谱仪广播；请检查 PC IP 24.5.10.100/8、防火墙和网线")
                elif kind == "parameters":
                    settings = event[1]
                    self.status.set(f"参数已应用到全部通道 · {settings['integration_us']} μs · 平均 {settings['averages']} 次")
                elif kind == "frame":
                    self._on_frame(*event[1:])
                elif kind == "capture_done":
                    args = list(event[1:])
                    while len(args) < 4:
                        args.append(None)
                    self._capture_finished(*args)
                elif kind == "error":
                    self.connect_button.configure(state="normal")
                    self.scan_button.configure(state="normal" if self.v["connection"].get() == "网络" else "disabled")
                    self.status_dot.configure(fg="#ef657e")
                    self.status.set(event[1])
                    messagebox.showerror("操作失败", event[1])
        except queue.Empty:
            pass
        self.after(70, self._poll_events)

    def _on_close(self) -> None:
        self.stop_event.set()
        if self.controller:
            self.controller.cancel_external_trigger()
            self.controller.disconnect()
        self.destroy()


def run() -> None:
    MariaApp().mainloop()
