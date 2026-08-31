from __future__ import annotations

import csv
import queue
import threading
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .controller import FeinixsController, StageError
from .paths import Point, filled_circle, offsets_from_start, raster, translate, validate_limits


class StageApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("LIBS Motion Control · 位移平台扫描系统")
        self.geometry("1380x820")
        self.minsize(980, 640)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.events: queue.Queue = queue.Queue()
        self.controller: FeinixsController | None = None
        self.points: list[Point] = []
        self.current_index = 0
        self.scanning = False
        self.scan_points: list[Point] = []
        self.active_relative_points: list[Point] = []
        self.completed_points = 0
        self.can_resume = False
        self.manual_moving = False
        self._build_vars()
        self._build_ui()
        self.after_idle(self._fit_to_screen)
        self.after(80, self._poll_events)
        self.generate_path()

    def _fit_to_screen(self):
        """以默认窗口大小居中显示，小屏幕上才按可用范围缩小。"""
        self.update_idletasks()
        screen_w, screen_h = self.winfo_screenwidth(), self.winfo_screenheight()
        width = max(980, min(1380, screen_w - 40))
        height = max(640, min(820, screen_h - 100))
        x = max(0, (screen_w - width) // 2)
        y = max(0, (screen_h - height) // 2 - 15)
        self.state("normal")
        self.geometry(f"{width}x{height}+{x}+{y}")

    def _build_vars(self):
        root = Path(__file__).resolve().parents[1]
        default_dll = root / "vendor_reference" / "ftcoreimc_win_v2.3.0.0n" / "ftcoreimc" / "lib" / "x64" / "ftcoreimc.dll"
        values = {
            "endpoint": "COM3", "baud": "115200", "dll": str(default_dll), "simulation": True,
            "path": "圆盘扫描", "radius": "10",
            "rows": "13", "row_points": "21", "line_spacing": "1.0", "point_spacing": "1.0",
            "velocity": "10.0", "dwell_ms": "0", "manual_step": "1.0", "manual_speed": "5.0",
            "xmin": "-50", "xmax": "50", "ymin": "-50", "ymax": "50",
        }
        self.v = {k: (tk.BooleanVar(value=x) if isinstance(x, bool) else tk.StringVar(value=x)) for k, x in values.items()}
        self.status = tk.StringVar(value="未连接（可使用仿真模式）")
        self.device_info = tk.StringVar(value="设备：未识别")
        self.position = tk.StringVar(value="X: 0.000 mm    Y: 0.000 mm    Z: 0.000 mm")
        self.progress = tk.DoubleVar(value=0)

    def _build_ui(self):
        self.configure(bg="#000000")
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TFrame", background="#000000")
        style.configure("Panel.TFrame", background="#080808")
        style.configure("TLabel", background="#080808", foreground="#d8e1e6", font=("Microsoft YaHei UI", 9))
        style.configure("Panel.TLabel", background="#080808", foreground="#d8e1e6")
        style.configure("Axis.TLabel", background="#080808", foreground="#40dcff", font=("Segoe UI", 10, "bold"), anchor="center")
        style.configure("Hint.TLabel", background="#080808", foreground="#77878f", font=("Microsoft YaHei UI", 8))
        style.configure("Title.TLabel", background="#000000", foreground="#40dcff", font=("Microsoft YaHei UI", 18, "bold"))
        style.configure("SubTitle.TLabel", background="#000000", foreground="#71818a", font=("Microsoft YaHei UI", 9))
        style.configure("Status.TLabel", background="#050505", foreground="#8de8ff", padding=8)
        style.configure("TLabelframe", background="#080808", bordercolor="#252525", relief="solid")
        style.configure("TLabelframe.Label", background="#080808", foreground="#40dcff", font=("Microsoft YaHei UI", 10, "bold"))
        style.configure("TEntry", fieldbackground="#111111", foreground="#f0f6f8", bordercolor="#303030", insertcolor="#40dcff", padding=5)
        style.configure("TCombobox", fieldbackground="#111111", foreground="#f0f6f8", arrowcolor="#40dcff", padding=4)
        style.map("TCombobox",
                  fieldbackground=[("readonly", "#111111"), ("focus", "#161616")],
                  foreground=[("readonly", "#f0f6f8")],
                  selectbackground=[("readonly", "#111111")],
                  selectforeground=[("readonly", "#f0f6f8")])
        self.option_add("*TCombobox*Listbox.background", "#111111")
        self.option_add("*TCombobox*Listbox.foreground", "#f0f6f8")
        self.option_add("*TCombobox*Listbox.selectBackground", "#007f9f")
        self.option_add("*TCombobox*Listbox.selectForeground", "#ffffff")
        style.configure("TCheckbutton", background="#080808", foreground="#c3cdd2")
        style.map("TCheckbutton", background=[("active", "#080808")])
        style.configure("TButton", background="#202020", foreground="#e8eef1", borderwidth=0, padding=(8, 4), font=("Microsoft YaHei UI", 9))
        style.map("TButton", background=[("active", "#353535"), ("pressed", "#141414")])
        style.configure("Accent.TButton", background="#00a8d6", foreground="#03121b", font=("Microsoft YaHei UI", 9, "bold"))
        style.map("Accent.TButton", background=[("active", "#38d5ff"), ("pressed", "#0086aa")])
        style.configure("Danger.TButton", background="#b93b55", foreground="white")
        style.map("Danger.TButton", background=[("active", "#e34f6b"), ("pressed", "#8f2940")])
        style.configure("Tech.Horizontal.TProgressbar", troughcolor="#151515", background="#00f0b5", bordercolor="#151515", lightcolor="#00f0b5", darkcolor="#00f0b5")
        style.configure("Tech.TSeparator", background="#252525")

        self.columnconfigure(0, minsize=260)
        self.columnconfigure(1, weight=1, minsize=480)
        self.columnconfigure(2, minsize=275)
        self.rowconfigure(1, weight=1)

        header = ttk.Frame(self, padding=(18, 13, 18, 10))
        header.grid(row=0, column=0, columnspan=3, sticky="ew")
        ttk.Label(header, text="LIBS MOTION CONTROL", style="Title.TLabel").pack(side="left")
        ttk.Label(header, text="双轴位移平台 · 智能扫描系统", style="SubTitle.TLabel").pack(side="left", padx=(16, 0), pady=(8, 0))
        ttk.Label(header, textvariable=self.device_info, style="SubTitle.TLabel", anchor="e").pack(side="right", pady=(8, 0))

        left = ttk.Frame(self, style="Panel.TFrame", padding=12)
        left.grid(row=1, column=0, sticky="nsew", padx=(12, 6), pady=0)
        right = ttk.Frame(self, style="Panel.TFrame", padding=12)
        right.grid(row=1, column=2, sticky="nsew", padx=(6, 12), pady=0)
        center = ttk.Frame(self, style="Panel.TFrame", padding=2)
        center.grid(row=1, column=1, sticky="nsew", padx=6)
        center.columnconfigure(0, weight=1); center.rowconfigure(1, weight=1)

        conn = ttk.LabelFrame(left, text=" 设备连接 ", padding=7); conn.pack(fill="x", pady=(0, 7))
        conn.columnconfigure(1, weight=1)
        self._entry(conn, "串口 / IP", "endpoint", 0); self._entry(conn, "波特率 / 端口", "baud", 1)
        self._entry(conn, "SDK DLL", "dll", 2, 20)
        ttk.Checkbutton(conn, text="仿真模式（不连接硬件）", variable=self.v["simulation"]).grid(row=3, column=0, columnspan=2, sticky="w", pady=(6, 2))
        ttk.Button(conn, text="连接设备", command=self.connect, style="Accent.TButton").grid(row=4, column=0, sticky="ew", padx=(0, 3), pady=(6, 0))
        ttk.Button(conn, text="断开连接", command=self.disconnect).grid(row=4, column=1, sticky="ew", padx=(3, 0), pady=(6, 0))

        adjust = ttk.LabelFrame(left, text=" 手动位置调整 ", padding=(7, 6)); adjust.pack(fill="x", pady=(0, 7))
        adjust.columnconfigure(0, minsize=46)
        adjust.columnconfigure((1, 2), weight=1, uniform="direction")

        settings = ttk.Frame(adjust, style="Panel.TFrame")
        settings.grid(row=0, column=0, columnspan=3, sticky="ew", pady=(0, 5))
        settings.columnconfigure((0, 1), weight=1, uniform="manual_setting")
        ttk.Label(settings, text="移动步长", style="Panel.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(settings, text="手动速度 (≤10)", style="Panel.TLabel").grid(row=0, column=1, sticky="w", padx=(8, 0))
        step_box = ttk.Frame(settings, style="Panel.TFrame")
        step_box.grid(row=1, column=0, sticky="ew", pady=(4, 0)); step_box.columnconfigure(0, weight=1)
        ttk.Entry(step_box, textvariable=self.v["manual_step"], width=7).grid(row=0, column=0, sticky="ew")
        ttk.Label(step_box, text=" mm", style="Hint.TLabel").grid(row=0, column=1)
        speed_box = ttk.Frame(settings, style="Panel.TFrame")
        speed_box.grid(row=1, column=1, sticky="ew", padx=(8, 0), pady=(4, 0)); speed_box.columnconfigure(0, weight=1)
        ttk.Entry(speed_box, textvariable=self.v["manual_speed"], width=7).grid(row=0, column=0, sticky="ew")
        ttk.Label(speed_box, text=" mm/s", style="Hint.TLabel").grid(row=0, column=1)

        ttk.Separator(adjust, orient="horizontal", style="Tech.TSeparator").grid(row=1, column=0, columnspan=3, sticky="ew", pady=(1, 4))
        ttk.Label(adjust, text="轴", style="Hint.TLabel", anchor="center").grid(row=2, column=0, sticky="ew")
        ttk.Label(adjust, text="负向移动", style="Hint.TLabel", anchor="center").grid(row=2, column=1, sticky="ew")
        ttk.Label(adjust, text="正向移动", style="Hint.TLabel", anchor="center").grid(row=2, column=2, sticky="ew")
        self.axis_buttons = {}
        for row, (label, axis) in enumerate((("X", "1"), ("Y", "2"), ("Z", "3")), 3):
            ttk.Label(adjust, text=label, style="Axis.TLabel").grid(row=row, column=0, sticky="nsew", pady=2)
            negative = ttk.Button(adjust, text="−  负向", command=lambda a=axis: self.manual_move(a, -1), state="disabled")
            positive = ttk.Button(adjust, text="+  正向", command=lambda a=axis: self.manual_move(a, 1), state="disabled")
            negative.grid(row=row, column=1, sticky="ew", padx=(0, 4), pady=2)
            positive.grid(row=row, column=2, sticky="ew", padx=(4, 0), pady=2)
            self.axis_buttons[axis] = (negative, positive)

        manual = ttk.LabelFrame(left, text=" 扫描控制 ", padding=7); manual.pack(fill="x")
        manual.columnconfigure((0, 1), weight=1)
        ttk.Button(manual, text="开始扫描", command=self.start_scan, style="Accent.TButton").grid(row=0, column=0, columnspan=2, sticky="ew")
        ttk.Button(manual, text="继续扫描", command=self.resume_scan).grid(row=1, column=0, columnspan=2, sticky="ew", pady=(4, 0))
        ttk.Button(manual, text="停止扫描", command=self.stop_scan, style="Danger.TButton").grid(row=2, column=0, columnspan=2, sticky="ew", pady=(4, 0))
        ttk.Button(manual, text="回零", command=self.home).grid(row=3, column=0, sticky="ew", padx=(0, 3), pady=(4, 0))
        ttk.Button(manual, text="当前位置置零", command=self.zero).grid(row=3, column=1, sticky="ew", padx=(3, 0), pady=(4, 0))
        ttk.Button(manual, text="导出路径 CSV", command=self.export_csv).grid(row=4, column=0, columnspan=2, sticky="ew", pady=(4, 0))

        prm = ttk.LabelFrame(right, text=" 扫描参数 ", padding=10); prm.pack(fill="x", pady=(0, 12))
        prm.columnconfigure(1, weight=1)
        ttk.Label(prm, text="路径类型", style="Panel.TLabel").grid(row=0, column=0, sticky="w")
        combo = ttk.Combobox(prm, textvariable=self.v["path"], values=("圆盘扫描", "栅格扫描"), state="readonly", width=15)
        combo.grid(row=0, column=1, sticky="ew")
        combo.bind("<<ComboboxSelected>>", lambda _: (self._update_param_visibility(), self.generate_path()))
        fields = [("圆盘半径 (mm)", "radius"), ("栅格纵向行数", "rows"), ("栅格横向点数", "row_points"),
                  ("径向/行间距 (mm)", "line_spacing"), ("扫描点间距 (mm)", "point_spacing"),
                  ("扫描速度 (mm/s, ≤10)", "velocity"), ("扫描点间停顿 (ms)", "dwell_ms")]
        self.path_param_widgets = {}
        for i, (label, key) in enumerate(fields, 1): self.path_param_widgets[key] = self._entry(prm, label, key, i)
        ttk.Button(prm, text="生成 / 刷新路径", command=self.generate_path, style="Accent.TButton").grid(row=len(fields)+1, column=0, columnspan=2, sticky="ew", pady=(9, 0))
        self._update_param_visibility()

        lim = ttk.LabelFrame(right, text=" 软件安全限位 ", padding=10); lim.pack(fill="x")
        lim.columnconfigure(1, weight=1)
        for i, (label, key) in enumerate((("X 最小 (mm)", "xmin"), ("X 最大 (mm)", "xmax"), ("Y 最小 (mm)", "ymin"), ("Y 最大 (mm)", "ymax"))): self._entry(lim, label, key, i)

        ttk.Label(center, text="SCAN PATH VISUALIZATION", style="Panel.TLabel", font=("Segoe UI", 10, "bold")).grid(row=0, column=0, sticky="w", padx=12, pady=(8, 6))
        self.canvas = tk.Canvas(center, bg="#000000", highlightthickness=1, highlightbackground="#303030")
        self.canvas.grid(row=1, column=0, sticky="nsew", padx=8, pady=(0, 8))
        self.canvas.bind("<Configure>", lambda _: self.draw_path())
        telemetry = ttk.Frame(center, style="Panel.TFrame", padding=(10, 5, 10, 10))
        telemetry.grid(row=2, column=0, sticky="ew"); telemetry.columnconfigure(0, weight=1)
        ttk.Progressbar(telemetry, variable=self.progress, maximum=100, style="Tech.Horizontal.TProgressbar").grid(row=0, column=0, sticky="ew")
        ttk.Label(telemetry, textvariable=self.position, style="Panel.TLabel", font=("Consolas", 12, "bold")).grid(row=1, column=0, sticky="w", pady=(7, 0))

        statusbar = ttk.Frame(self, style="Panel.TFrame")
        statusbar.grid(row=2, column=0, columnspan=3, sticky="ew", padx=12, pady=(8, 10))
        ttk.Label(statusbar, text="● SYSTEM", style="Status.TLabel").pack(side="left")
        ttk.Label(statusbar, textvariable=self.status, style="Status.TLabel").pack(side="left", fill="x", expand=True)

    def _entry(self, parent, label, key, row, width=16):
        label_widget = ttk.Label(parent, text=label)
        entry_widget = ttk.Entry(parent, textvariable=self.v[key], width=width)
        label_widget.grid(row=row, column=0, sticky="w", padx=(0, 6), pady=2)
        entry_widget.grid(row=row, column=1, sticky="ew", pady=2)
        return label_widget, entry_widget

    def _update_param_visibility(self):
        if not hasattr(self, "path_param_widgets"):
            return
        kind = self.v["path"].get()
        spacing_label = "径向环间距 (mm)" if kind == "圆盘扫描" else "栅格行间距 (mm)"
        self.path_param_widgets["line_spacing"][0].configure(text=spacing_label)
        visible = {"line_spacing", "point_spacing", "velocity", "dwell_ms"}
        visible |= {"radius"} if kind == "圆盘扫描" else {"rows", "row_points"}
        for key, widgets in self.path_param_widgets.items():
            for widget in widgets:
                widget.grid() if key in visible else widget.grid_remove()

    def _number(self, key, cast=float):
        try: return cast(self.v[key].get())
        except ValueError: raise ValueError(f"参数“{key}”不是有效数字")

    def _speed_is_safe(self, speed: float, name: str) -> bool:
        maximum = FeinixsController.SAFE_MAX_VELOCITY
        if speed > maximum:
            messagebox.showwarning(
                "速度过快",
                f"{name} {speed:g} mm/s 超过软件安全上限 {maximum:g} mm/s。\n"
                "为保护位移平台，本次运动不会启动，请降低速度。",
            )
            return False
        return True

    def generate_path(self):
        try:
            kind = self.v["path"].get()
            if kind == "圆盘扫描":
                geometry = filled_circle(0.0, 0.0, self._number("radius"), self._number("line_spacing"), self._number("point_spacing"))
            else:
                geometry = raster(0.0, 0.0, self._number("rows", int), self._number("row_points", int), self._number("line_spacing"), self._number("point_spacing"))
            self.points = offsets_from_start(geometry)
            self.current_index = 0; self.progress.set(0); self.draw_path()
            self.status.set(f"相对路径已生成：{kind}，共 {len(self.points)} 点；起点为平台当前位置")
            return True
        except ValueError as e:
            messagebox.showerror("路径参数错误", str(e)); return False

    def draw_path(self):
        c = self.canvas; c.delete("all")
        w, h = max(c.winfo_width(), 100), max(c.winfo_height(), 100); pad = 45
        for x in range(20, w, 40): c.create_line(x, 0, x, h, fill="#121212")
        for y in range(20, h, 40): c.create_line(0, y, w, y, fill="#121212")
        c.create_line(pad, h-pad, w-pad, h-pad, fill="#3a3a3a", arrow="last")
        c.create_line(pad, h-pad, pad, pad, fill="#3a3a3a", arrow="last")
        if not self.points: return
        xs, ys = [p.x for p in self.points], [p.y for p in self.points]
        xmin, xmax, ymin, ymax = min(xs), max(xs), min(ys), max(ys)
        spanx, spany = max(xmax-xmin, 1), max(ymax-ymin, 1); scale = min((w-2*pad)/spanx, (h-2*pad)/spany)
        xy = lambda p: (pad+(p.x-xmin)*scale, h-pad-(p.y-ymin)*scale)
        coords = [v for p in self.points for v in xy(p)]
        if len(coords) >= 4:
            c.create_line(*coords, fill="#16252a", width=4)
            c.create_line(*coords, fill="#31505a", width=1)
            if self.completed_points > 1:
                completed_coords = coords[:self.completed_points * 2]
                c.create_line(*completed_coords, fill="#006b50", width=6)
                c.create_line(*completed_coords, fill="#00f0b5", width=2)
        for i, p in enumerate(self.points):
            x, y = xy(p)
            color = "#00d69f" if i < self.completed_points else "#42606a"
            radius = 3 if i < self.completed_points else 2
            c.create_oval(x-radius, y-radius, x+radius, y+radius, fill=color, outline="")
        if self.completed_points > 0 and 0 <= self.current_index < len(self.points):
            x, y = xy(self.points[self.current_index])
            c.create_oval(x-11, y-11, x+11, y+11, outline="#593400", width=3)
            c.create_oval(x-8, y-8, x+8, y+8, outline="#ff9d00", width=2)
            c.create_oval(x-4, y-4, x+4, y+4, fill="#fff200", outline="#ffffff", width=1)
            c.create_line(x-15, y, x-7, y, fill="#ffcf40", width=1)
            c.create_line(x+7, y, x+15, y, fill="#ffcf40", width=1)
            c.create_line(x, y-15, x, y-7, fill="#ffcf40", width=1)
            c.create_line(x, y+7, x, y+15, fill="#ffcf40", width=1)
            label_anchor = "sw" if x < w-120 else "se"
            label_x = x + 15 if x < w-120 else x - 15
            c.create_text(label_x, y-15, anchor=label_anchor, fill="#fff200",
                          font=("Microsoft YaHei UI", 8, "bold"),
                          text=f"当前点  {self.completed_points}/{len(self.points)}")
        c.create_text(12, 12, anchor="nw", fill="white", text=f"{self.v['path'].get()} | {len(self.points)} 点 | 相对位移 mm | ΔX [{xmin:.2f}, {xmax:.2f}]  ΔY [{ymin:.2f}, {ymax:.2f}]")
        c.create_text(w-12, h-12, anchor="se", fill="#adb5bd", text="X →    Y ↑")

    def connect(self):
        try:
            if self.controller: self.controller.close()
            self.controller = FeinixsController(self.v["dll"].get(), self.v["simulation"].get())
            self.controller.connect(self.v["endpoint"].get().strip(), self._number("baud", int))
            labels = {"1": "X", "2": "Y", "3": "Z"}
            models = []
            for axis in ("1", "2", "3"):
                available = axis in self.controller.available_axes
                for button in self.axis_buttons[axis]:
                    button.configure(state="normal" if available else "disabled")
                model = self.controller.axis_info.get(axis, {}).get("model", "未识别")
                models.append(f"{labels[axis]}: {model}")
            self.device_info.set("设备  " + "  |  ".join(models))
            self.position.set(self._position_text())
            mode = "仿真" if self.v["simulation"].get() else "实机"
            axes = "/".join(labels[a] for a in sorted(self.controller.available_axes))
            self.status.set(f"已连接（{mode}模式），识别轴：{axes}；连接初始位置已置零")
        except Exception as e:
            self.device_info.set("设备：识别失败")
            self.position.set("X: 0.000 mm    Y: 0.000 mm    Z: 0.000 mm")
            for buttons in self.axis_buttons.values():
                for button in buttons: button.configure(state="disabled")
            messagebox.showerror("连接失败", str(e))

    def disconnect(self):
        if self.controller: self.controller.stop(); self.controller.close()
        self.device_info.set("设备：未识别")
        self.position.set("X: 0.000 mm    Y: 0.000 mm    Z: 0.000 mm")
        for buttons in self.axis_buttons.values():
            for button in buttons: button.configure(state="disabled")
        self.status.set("已断开")

    def _position_text(self) -> str:
        if not self.controller or not self.controller.connected:
            return "X: 0.000 mm    Y: 0.000 mm    Z: 0.000 mm"
        values = []
        for axis, name in (("1", "X"), ("2", "Y"), ("3", "Z")):
            value = f"{self.controller.positions[axis]:.3f}" if axis in self.controller.available_axes else "--"
            values.append(f"{name}: {value} mm")
        return "    ".join(values)

    def manual_move(self, axis: str, direction: int):
        if self.scanning:
            messagebox.showinfo("扫描正在进行", "扫描期间不能手动移动位移平台。")
            return
        if self.manual_moving:
            messagebox.showinfo("手动移动中", "请等待当前手动移动完成。")
            return
        if not self.controller or not self.controller.connected:
            messagebox.showwarning("未连接", "请先连接位移平台或启用仿真模式")
            return
        try:
            step = self._number("manual_step")
            speed = self._number("manual_speed")
            if step <= 0 or speed <= 0:
                raise ValueError("手动步长和手动速度必须大于 0")
            if not self._speed_is_safe(speed, "手动速度"):
                return
            axis_limits = {"1": (self._number("xmin"), self._number("xmax")),
                           "2": (self._number("ymin"), self._number("ymax"))}.get(axis)
        except ValueError as e:
            messagebox.showerror("参数错误", str(e)); return
        self.manual_moving = True
        axis_name = {"1": "X", "2": "Y", "3": "Z"}[axis]
        self.status.set(f"手动移动 {axis_name} 轴……")
        threading.Thread(target=self._manual_worker, args=(axis, direction * step, speed, axis_limits), daemon=True).start()

    def _manual_worker(self, axis: str, distance: float, speed: float, axis_limits):
        try:
            current = self.controller.get_position(axis)
            target = current + distance
            if axis == "1" and not axis_limits[0] <= target <= axis_limits[1]:
                direction = "负向" if target < axis_limits[0] else "正向"
                limit = axis_limits[0] if target < axis_limits[0] else axis_limits[1]
                raise ValueError(f"X轴已达到{direction}软件极限 {limit:.3f} mm，不能继续移动")
            if axis == "2" and not axis_limits[0] <= target <= axis_limits[1]:
                direction = "负向" if target < axis_limits[0] else "正向"
                limit = axis_limits[0] if target < axis_limits[0] else axis_limits[1]
                raise ValueError(f"Y轴已达到{direction}软件极限 {limit:.3f} mm，不能继续移动")
            position = self.controller.move_axis_relative(axis, distance, speed)
            self.events.put(("manual_done", axis, position))
        except Exception as e:
            self.events.put(("manual_error", str(e)))

    def start_scan(self):
        if self.scanning:
            messagebox.showinfo("扫描正在进行", "当前扫描尚未结束，请勿重复启动。")
            return
        if self.manual_moving:
            messagebox.showinfo("手动移动中", "请等待手动移动完成后再开始扫描。")
            return
        if not self.generate_path(): return
        if not self.controller or not self.controller.connected:
            messagebox.showwarning("未连接", "请先连接位移平台或启用仿真模式"); return
        if not {"1", "2"}.issubset(self.controller.available_axes):
            messagebox.showerror("轴识别不完整", "圆盘和栅格扫描需要同时识别X轴（地址1）和Y轴（地址2）。")
            return
        velocity, dwell_ms = self._number("velocity"), self._number("dwell_ms", int)
        if velocity <= 0 or dwell_ms < 0: messagebox.showerror("参数错误", "速度必须大于 0，扫描点间停顿不能为负"); return
        if not self._speed_is_safe(velocity, "扫描速度"): return
        dwell = dwell_ms / 1000.0
        try:
            start_x = self.controller.get_position("1")
            start_y = self.controller.get_position("2")
            self.scan_points = translate(self.points, start_x, start_y)
            validate_limits(self.scan_points, (self._number("xmin"), self._number("xmax")), (self._number("ymin"), self._number("ymax")))
        except Exception as e:
            messagebox.showerror("无法开始扫描", str(e)); return
        self.completed_points = 0
        self.can_resume = False
        self.active_relative_points = list(self.points)
        self.current_index = 0
        self.progress.set(0)
        self.scanning = True
        self.status.set(f"扫描运行中……起点 X={start_x:.3f} mm, Y={start_y:.3f} mm")
        threading.Thread(target=self._scan_worker, args=(self.scan_points, velocity, dwell, 0), daemon=True).start()

    def resume_scan(self):
        if self.scanning:
            messagebox.showinfo("扫描正在进行", "当前扫描尚未结束，请勿重复启动。")
            return
        if self.manual_moving:
            messagebox.showinfo("手动移动中", "请等待手动移动完成后再继续扫描。")
            return
        if not self.controller or not self.controller.connected:
            messagebox.showwarning("未连接", "请先连接位移平台或启用仿真模式")
            return
        if not {"1", "2"}.issubset(self.controller.available_axes):
            messagebox.showerror("轴识别不完整", "继续路径扫描需要同时识别X轴（地址1）和Y轴（地址2）。")
            return
        if not self.can_resume or self.completed_points >= len(self.scan_points):
            messagebox.showinfo("无法继续", "没有可继续的已停止扫描，请选择从当前位置重新扫描。")
            return
        velocity, dwell_ms = self._number("velocity"), self._number("dwell_ms", int)
        if velocity <= 0 or dwell_ms < 0:
            messagebox.showerror("参数错误", "速度必须大于 0，扫描点间停顿不能为负")
            return
        if not self._speed_is_safe(velocity, "扫描速度"): return
        dwell = dwell_ms / 1000.0
        self.points = list(self.active_relative_points)
        self.draw_path()
        remaining = self.scan_points[self.completed_points:]
        self.scanning = True
        self.can_resume = False
        self.status.set(f"继续扫描：从第 {self.completed_points + 1}/{len(self.scan_points)} 点开始")
        threading.Thread(target=self._scan_worker, args=(remaining, velocity, dwell, self.completed_points), daemon=True).start()

    def _scan_worker(self, scan_points, velocity, dwell, index_offset):
        try:
            self.controller.run_path(scan_points, velocity, dwell,
                                     lambda i, p: self.events.put(("point", i + index_offset, p)))
            self.events.put(("done",))
        except Exception as e: self.events.put(("error", str(e)))

    def _poll_events(self):
        try:
            while True:
                event = self.events.get_nowait()
                if event[0] == "point":
                    _, i, p = event; self.completed_points = i; self.current_index = i-1; self.progress.set(i/len(self.points)*100)
                    self.position.set(self._position_text()); self.status.set(f"扫描中：{i}/{len(self.points)}")
                    self.draw_path()
                elif event[0] == "manual_done":
                    self.manual_moving = False
                    _, axis, pos = event
                    self.position.set(self._position_text())
                    self.status.set(f"{ {'1':'X', '2':'Y', '3':'Z'}[axis] } 轴手动移动完成：{pos:.3f} mm")
                elif event[0] == "manual_error":
                    self.manual_moving = False
                    self.status.set(event[1]); messagebox.showwarning("手动移动结束", event[1])
                elif event[0] == "coordinate_done":
                    self.manual_moving = False
                    _, action, xyz = event
                    self.position.set(self._position_text())
                    self.status.set(f"{action}完成，坐标已刷新")
                elif event[0] == "coordinate_error":
                    self.manual_moving = False
                    self.status.set(event[1]); messagebox.showerror("轴操作失败", event[1])
                elif event[0] == "done":
                    self.scanning = False
                    self.can_resume = False
                    self.status.set("扫描完成")
                else:
                    self.scanning = False
                    self.can_resume = "扫描已停止" in event[1] and self.completed_points < len(self.scan_points)
                    if self.can_resume:
                        self.status.set(f"扫描已停止：完成 {self.completed_points}/{len(self.scan_points)} 点，可继续或重新扫描")
                    else:
                        self.status.set(event[1])
                    messagebox.showwarning("扫描结束", event[1])
        except queue.Empty: pass
        self.after(80, self._poll_events)

    def stop_scan(self):
        if self.controller: self.controller.stop()
        self.status.set("已发送停止命令")

    def home(self):
        self._coordinate_action("所有已连接轴回零", self.controller.home if self.controller else None)

    def zero(self):
        self._coordinate_action("所有已连接轴当前位置置零", self.controller.zero if self.controller else None)

    def _coordinate_action(self, name, fn):
        if not self.controller or not self.controller.connected:
            messagebox.showwarning("未连接", "请先连接位移平台"); return
        if self.scanning:
            messagebox.showinfo("扫描正在进行", f"扫描期间不能执行{name}。")
            return
        if self.manual_moving:
            messagebox.showinfo("轴操作进行中", "请等待当前轴操作完成。")
            return
        self.manual_moving = True
        self.status.set(f"正在{name}……")
        threading.Thread(target=self._coordinate_worker, args=(name, fn), daemon=True).start()

    def _coordinate_worker(self, name, fn):
        try:
            xyz = fn()
            self.events.put(("coordinate_done", name, xyz))
        except Exception as e:
            self.events.put(("coordinate_error", str(e)))

    def export_csv(self):
        if not self.points and not self.generate_path(): return
        default = f"scan_path_{datetime.now():%Y%m%d_%H%M%S}.csv"
        path = filedialog.asksaveasfilename(defaultextension=".csv", initialfile=default, filetypes=[("CSV", "*.csv")])
        if path:
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.writer(f); writer.writerow(["index", "delta_x_mm", "delta_y_mm"])
                writer.writerows((i, p.x, p.y) for i, p in enumerate(self.points, 1))
            self.status.set(f"路径已导出：{path}")

    def _on_close(self):
        if self.controller: self.controller.stop(); self.controller.close()
        self.destroy()


def run():
    StageApp().mainloop()
