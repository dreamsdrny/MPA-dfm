"""Gerber / ODB++ 图形查看器：后台光栅化 + 单张位图替换，避免逐条重绘的卡顿与残影。

渲染只在后台线程算 PNG 字节，Tk 侧只替换一整张图片；视野变化用 after 合并成一次请求，
过期帧按 revision 丢弃，所以拖动、滚轮放大不会留下上一帧，也不会排队几十次重绘。
"""
import queue
import threading
from io import BytesIO

import tkinter as tk
from tkinter import ttk

from PIL import Image, ImageTk

from src.application.geometry_view import pan_viewport, viewport, zoom_viewport
from src.ui.theme import COLORS

ROLE_LABELS = {"copper": "铜层", "mask": "阻焊", "paste": "锡膏", "silkscreen": "丝印",
               "outline": "板框", "drill": "钻孔", "unknown": "其他"}


class GeometryViewer(ttk.Frame):
    def __init__(self, master, on_close=None):
        super().__init__(master, style="Card.TFrame", padding=(8, 6))
        self.on_close = on_close
        self.model = None
        self.area = None
        self.revision = 0
        self._job = None
        self._poll_id = None
        self._photo = None
        self._drag = None
        self._alive = True
        self._fullscreen = False
        self._interactive = False
        self._requests = queue.Queue()
        self._results = queue.Queue()
        self._worker = None
        self.bind("<Destroy>", self._destroyed, add="+")

        bar = ttk.Frame(self, style="Card.TFrame")
        bar.pack(fill=tk.X)
        for label, command, width in (("适应窗口", self.fit, 10), ("＋", lambda: self._zoom(1.6), 4),
                                      ("－", lambda: self._zoom(1 / 1.6), 4), ("全屏", self.toggle_fullscreen, 10)):
            ttk.Button(bar, text=label, width=width, command=command).pack(side=tk.LEFT, padx=(0, 4))
        self.scale_var = tk.StringVar(value="未载入图形")
        ttk.Label(bar, textvariable=self.scale_var, style="CardHint.TLabel").pack(side=tk.RIGHT)
        self.status_var = tk.StringVar(value="滚轮缩放、拖动平移、双击适应窗口；图形仅用于查看，不参与判定")
        ttk.Label(self, textvariable=self.status_var, style="CardHint.TLabel", wraplength=980,
                  justify=tk.LEFT).pack(fill=tk.X, pady=(4, 2))

        body = ttk.Frame(self, style="Card.TFrame")
        body.pack(fill=tk.BOTH, expand=True)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)
        side = ttk.Frame(body, style="Card.TFrame")
        side.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        side.columnconfigure(0, weight=1)
        self.layers = ttk.Treeview(side, columns=("role", "features", "points"), show="tree headings", height=12)
        self.layers.heading("#0", text="图层（点选切换显隐）")
        self.layers.column("#0", width=180, minwidth=120)
        for key, title, width in (("role", "角色", 66), ("features", "图形数", 66), ("points", "显示点数", 78)):
            self.layers.heading(key, text=title)
            self.layers.column(key, width=width, minwidth=44, anchor="e")
        self.layers.grid(row=0, column=0, sticky="nsew")
        self.layers.bind("<Button-1>", self._layer_click)
        self.issues = ttk.Treeview(side, columns=("item", "xy"), show="headings", height=8)
        for key, title, width in (("item", "问题（点选定位）", 200), ("xy", "X / Y mm", 120)):
            self.issues.heading(key, text=title)
            self.issues.column(key, width=width, minwidth=60)
        self.issues.grid(row=1, column=0, sticky="nsew", pady=(6, 0))
        self.issues.bind("<<TreeviewSelect>>", self._issue_selected)

        self.canvas = tk.Canvas(body, bg=COLORS["card"], highlightthickness=1, highlightbackground=COLORS["border"])
        self.canvas.grid(row=0, column=1, sticky="nsew")
        self._image = self.canvas.create_image(0, 0, anchor="nw", image="")
        self.canvas.bind("<Configure>", lambda _event: self._schedule())
        self.canvas.bind("<MouseWheel>", self._wheel)
        self.canvas.bind("<Button-4>", lambda event: self._wheel(event, 1.25))
        self.canvas.bind("<Button-5>", lambda event: self._wheel(event, 1 / 1.25))
        self.canvas.bind("<ButtonPress-1>", self._grab)
        self.canvas.bind("<B1-Motion>", self._move)
        self.canvas.bind("<ButtonRelease-1>", lambda _event: self._settle())
        self.canvas.bind("<Double-1>", lambda _event: self.fit())
        self.bind("<KeyRelease-Escape>", lambda _event: self._exit_fullscreen())

    # ------------------------------------------------------------------ 数据
    def set_model(self, model, title=""):
        self.model = model
        self.area = viewport(model)
        self.layers.delete(*self.layers.get_children())
        for layer in (model or {}).get("layers", []):
            family = str(layer.get("role", "")).split("_")[0].split("/")[-1]
            self.layers.insert("", tk.END, iid=layer["name"],
                               text=("● " if layer.get("visible") else "○ ") + layer["name"],
                               values=(ROLE_LABELS.get(family, family), layer.get("features", 0),
                                       layer.get("points", 0)))
        self.issues.delete(*self.issues.get_children())
        for issue in (model or {}).get("issues", []):
            self.issues.insert("", tk.END, iid=str(issue["index"]),
                               values=(f"{issue['index']}. {issue['label']}", f"{issue['x_mm']:g} / {issue['y_mm']:g}"))
        notes = [str(row) for row in (model or {}).get("notes", [])[:2]]
        warnings = [str(row) for row in (model or {}).get("warnings", [])[:2]]
        text = str(title or "")
        for row in notes + warnings:
            if row not in text:  # 调用方已把说明带进来时不重复拼接
                text = (text + "；" + row) if text else row
        self.status_var.set(text or "滚轮缩放、拖动平移、双击适应窗口；图形仅用于查看，不参与判定")
        self._ensure_worker()
        self._schedule(immediate=True)

    def visible_layer_names(self):
        if not self.model:
            return []
        return [layer["name"] for layer in self.model["layers"] if layer.get("visible")]

    # ------------------------------------------------------------------ 交互
    def fit(self):
        self.area = viewport(self.model)
        self._schedule(immediate=True)

    def _zoom(self, factor, anchor=None):
        if not self.area:
            return
        self.area = zoom_viewport(self.area, factor, anchor)
        self._schedule()

    def _wheel(self, event, factor=None):
        if not self.area:
            return
        self._interactive = True
        self._zoom(factor or (1.25 if event.delta > 0 else 1 / 1.25), self._to_mm(event.x, event.y))

    def _grab(self, event):
        self._drag = (event.x, event.y)
        self._interactive = True

    def _move(self, event):
        if not self._drag or not self.area:
            return
        width = max(self.canvas.winfo_width(), 1)
        height = max(self.canvas.winfo_height(), 1)
        self.area = pan_viewport(self.area, (-(event.x - self._drag[0]) * (self.area[2] - self.area[0]) / width,
                                             (event.y - self._drag[1]) * (self.area[3] - self.area[1]) / height))
        self._drag = (event.x, event.y)
        self._schedule()

    def _settle(self):
        self._drag = None
        self._interactive = False
        self._schedule(immediate=True)

    def _to_mm(self, px, py):
        if not self.area:
            return None
        width = max(self.canvas.winfo_width(), 2)
        height = max(self.canvas.winfo_height(), 2)
        scale = min((width - 16) / max(self.area[2] - self.area[0], 1e-6),
                    (height - 40) / max(self.area[3] - self.area[1], 1e-6))
        offset_x = (width - (self.area[2] - self.area[0]) * scale) / 2
        offset_y = (height - (self.area[3] - self.area[1]) * scale) / 2 + 16
        return (self.area[0] + (px - offset_x) / scale, self.area[3] - (py - offset_y) / scale)

    def _layer_click(self, event):
        row = self.layers.identify_row(event.y)
        if not row:
            return
        self.toggle_layer(row)

    def toggle_layer(self, name):
        """切换图层显隐；单独暴露给测试与键盘操作，不依赖鼠标命中坐标。"""
        if not self.model:
            return
        layer = next((row for row in self.model["layers"] if row["name"] == name), None)
        if layer is None:
            return
        layer["visible"] = not layer["visible"]
        if self.layers.exists(name):
            self.layers.item(name, text=("● " if layer["visible"] else "○ ") + name)
        self._schedule(immediate=True)
        return layer["visible"]

    def render_now(self):
        """同步渲染一次（供诊断/测试使用）；正常交互仍走后台线程。"""
        from src.application.geometry_view import render_view
        size = (max(self.canvas.winfo_width(), 64), max(self.canvas.winfo_height(), 64))
        layers = [layer for layer in self.model["layers"] if layer["name"] in self.visible_layer_names()]
        return render_view(self.model, self.area, size, layers=layers)

    def _issue_selected(self, _event=None):
        chosen = self.issues.selection()
        if not chosen or not self.model or not self.area:
            return
        issue = next((row for row in self.model["issues"] if str(row["index"]) == chosen[0]), None)
        if not issue:
            return
        span = max(self.area[2] - self.area[0], 10) / 4
        self.area = [issue["x_mm"] - span, issue["y_mm"] - span, issue["x_mm"] + span, issue["y_mm"] + span]
        self._schedule(immediate=True)

    def toggle_fullscreen(self):
        top = self.winfo_toplevel()
        self._fullscreen = not self._fullscreen
        top.attributes("-fullscreen", self._fullscreen)
        if not self._fullscreen:
            top.geometry("1180x760")
        self._schedule(immediate=True)
        return self._fullscreen

    def _exit_fullscreen(self):
        if self._fullscreen:
            self.toggle_fullscreen()

    # ------------------------------------------------------------------ 渲染
    def _schedule(self, immediate=False):
        if not self.model or not self.winfo_exists():
            return
        if self._job is not None:
            try:
                self.after_cancel(self._job)
            except tk.TclError:
                self._job = None
        self._job = None
        try:
            self._job = self.after(30 if immediate else (60 if self._interactive else 120), self._request)
        except tk.TclError:
            pass

    def _request(self):
        self._job = None
        if not self._alive or not self.model or not self.winfo_exists():
            return
        width = max(self.canvas.winfo_width(), 64)
        height = max(self.canvas.winfo_height(), 64)
        size = (width, height) if not self._interactive else (max(200, width // 3), max(140, height // 3))
        self.revision += 1
        self._requests.put((self.revision, list(self.area), tuple(size), tuple(self.visible_layer_names())))
        self._ensure_worker()

    def _ensure_worker(self):
        if self._worker is None or not self._worker.is_alive():
            self._worker = threading.Thread(target=self._render_loop, daemon=True)
            self._worker.start()
        if self._poll_id is None and self.winfo_exists():
            self._poll_id = self.after(40, self._drain)

    def _render_loop(self):
        from src.application import geometry_view
        while True:
            try:
                revision, area, size, names = self._requests.get(timeout=.25)
            except queue.Empty:
                if not self._alive:
                    return
                continue
            if not self._alive:
                return
            try:
                layers = [layer for layer in self.model["layers"] if layer["name"] in names]
                # 每次按模块属性取函数：测试/诊断替换实现后，后台线程不会继续用旧绑定。
                data, stats = geometry_view.render_view(self.model, area, size, layers=layers)
                outcome = (revision, "ok", data, stats, size)
            except Exception as exc:  # noqa: BLE001  渲染失败要说明原因，不能留旧帧冒充当前视野
                outcome = (revision, "error", str(exc), None, size)
            self._results.put(outcome)

    def _drain(self):
        self._poll_id = None
        if not self._alive:
            return
        latest = None
        while True:
            try:
                outcome = self._results.get_nowait()
            except queue.Empty:
                break
            latest = outcome
        if latest is not None:
            revision, kind, payload, stats, size = latest
            if revision == self.revision and self.winfo_exists():
                if kind == "error":
                    self.canvas.itemconfigure(self._image, image="")
                    self._photo = None
                    self.scale_var.set("渲染失败")
                    self.status_var.set("图形渲染失败：" + payload + "；未显示旧帧冒充当前视野")
                else:
                    try:
                        # 绑定到本画布的 Tcl 解释器，避免多根窗口时图片句柄错位。
                        self._photo = ImageTk.PhotoImage(Image.open(BytesIO(payload)), master=self.canvas)
                        self.canvas.itemconfigure(self._image, image=self._photo)
                        self.canvas.coords(self._image, 0, 0)
                    except tk.TclError as exc:
                        self._photo = None
                        self.status_var.set("图形已渲染但窗口状态变化，未替换画面：" + str(exc))
                        # 不 return：轮询器仍需继续，否则后续帧永远不来
                    self.scale_var.set(f"{size[0]}×{size[1]} px · {stats['mm_per_pixel']:.4f} mm/px · "
                                       f"图层 {stats['drawn_layers']} · 问题 {stats['issues']}"
                                       + (" · 显示已简化" if stats.get("note") else ""))
                    current = (max(self.canvas.winfo_width(), 64), max(self.canvas.winfo_height(), 64))
                    if abs(current[0] - size[0]) > 2 or abs(current[1] - size[1]) > 2:
                        # 首次渲染可能发生在窗口映射前；尺寸变了就自动补一帧，避免留下小图。
                        self._schedule(immediate=True)
        if self._requests.qsize() or self._results.qsize():
            self._poll_id = self.after(20, self._drain)
        else:
            self._poll_id = self.after(80, self._drain)

    def _destroyed(self, event):
        if event.widget is not self:
            return
        self._alive = False
        for handle in (self._job, self._poll_id):
            if handle is not None:
                try:
                    self.after_cancel(handle)
                except tk.TclError:
                    pass
        self._job = self._poll_id = None
        if self.on_close:
            self.on_close()


class ViewerWindow(tk.Toplevel):
    """独立查看窗口：不挤占主窗口空间，可全屏；关闭只销毁本窗口。"""

    def __init__(self, parent, model, title="图形查看器", subtitle=""):
        super().__init__(parent)
        self.title(title)
        self.geometry("1180x760")
        self.viewer = GeometryViewer(self)
        self.viewer.pack(fill=tk.BOTH, expand=True)
        self.viewer.set_model(model, subtitle)
        self.bind("<Escape>", lambda _event: self.destroy())
        self.transient(parent)


def open_viewer(parent, model, title="图形查看器", subtitle=""):
    if not model or not model.get("layers"):
        return None
    window = ViewerWindow(parent, model, title, subtitle)
    window.lift()
    return window
