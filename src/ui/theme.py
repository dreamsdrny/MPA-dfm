"""界面外观: 配色 / 字体 / ttk 样式 / 卡片 / 日志控件.

只依赖 tkinter, 不含业务逻辑。目标是让 tkinter 界面接近现代桌面工具的观感:
深色页眉 + 浅灰底 + 白色卡片 + 蓝色主操作 + 深色日志区。
"""
import sys
import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk

COLORS = {
    "bg": "#F3F5F9",
    "card": "#FFFFFF",
    "border": "#E1E6EF",
    "text": "#1F2937",
    "muted": "#6B7280",
    "faint": "#94A3B8",
    "primary": "#2563EB",
    "primary_dark": "#1D4ED8",
    "primary_soft": "#E8EEFB",
    "success": "#15803D",
    "warn": "#B45309",
    "danger": "#B91C1C",
    "header": "#0F172A",
    "header_text": "#F8FAFC",
    "header_muted": "#94A3B8",
    "log_bg": "#0B1220",
    "log_fg": "#CBD5E1",
    "status_bg": "#E8EDF6",
    "row_alt": "#F8FAFC",
}

# 优先使用系统中文字体, 缺失时退回 Tk 默认字体
_FONT_CANDIDATES = ("Microsoft YaHei UI", "Microsoft YaHei", "PingFang SC",
                    "Noto Sans CJK SC", "Segoe UI")
_MONO_CANDIDATES = ("Cascadia Mono", "Consolas", "DejaVu Sans Mono", "Courier New")

_ui_family = None
_mono_family = None


def _resolve_family(root, candidates, fallback):
    try:
        installed = set(tkfont.families(root))
    except Exception:  # noqa: BLE001
        return fallback
    for name in candidates:
        if name in installed:
            return name
    return fallback


def fonts(root) -> dict:
    """返回界面字体字典 (按 root 的可用字体解析一次)。"""
    global _ui_family, _mono_family
    if _ui_family is None:
        _ui_family = _resolve_family(root, _FONT_CANDIDATES, "TkDefaultFont")
    if _mono_family is None:
        _mono_family = _resolve_family(root, _MONO_CANDIDATES, "Courier")
    return {
        "ui": (_ui_family, 9),
        "ui_bold": (_ui_family, 9, "bold"),
        "small": (_ui_family, 8),
        "title": (_ui_family, 16, "bold"),
        "subtitle": (_ui_family, 9),
        "section": (_ui_family, 10, "bold"),
        "button": (_ui_family, 9, "bold"),
        "mono": (_mono_family, 9),
    }


def enable_dpi_awareness() -> None:
    """Windows 下开启 DPI 感知, 避免高分屏文字发虚。"""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:  # noqa: BLE001
        try:
            import ctypes
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:  # noqa: BLE001
            pass


def apply_theme(root) -> ttk.Style:
    """给 root 套用统一主题, 返回 Style 以便后续微调。"""
    f = fonts(root)
    style = ttk.Style(root)
    try:
        style.theme_use("clam")          # clam 对自定义颜色的支持最好
    except tk.TclError:
        pass

    bg, card, border = COLORS["bg"], COLORS["card"], COLORS["border"]
    text, muted = COLORS["text"], COLORS["muted"]

    style.configure(".", background=bg, foreground=text, font=f["ui"],
                    bordercolor=border, focuscolor=COLORS["primary"])

    # ---- 基础容器 ----
    style.configure("TFrame", background=bg)
    style.configure("Card.TFrame", background=card)
    style.configure("Header.TFrame", background=COLORS["header"])

    # ---- 文字 ----
    style.configure("TLabel", background=bg, foreground=text)
    style.configure("Card.TLabel", background=card, foreground=text)
    style.configure("CardTitle.TLabel", background=card, foreground=text,
                    font=f["section"])
    style.configure("CardHint.TLabel", background=card, foreground=muted,
                    font=f["small"])
    style.configure("FieldLabel.TLabel", background=card, foreground=muted,
                    font=f["small"])
    style.configure("Status.TLabel", background=COLORS["status_bg"],
                    foreground=COLORS["muted"], font=f["small"], padding=(12, 7))
    style.configure("HeaderTitle.TLabel", background=COLORS["header"],
                    foreground=COLORS["header_text"], font=f["title"])
    style.configure("HeaderSub.TLabel", background=COLORS["header"],
                    foreground=COLORS["header_muted"], font=f["subtitle"])
    style.configure("Value.TLabel", background=card, foreground=text,
                    font=f["ui_bold"])
    style.configure("KPI.TLabel", background=card, foreground=COLORS["primary_dark"],
                    font=(f["ui"][0], 15, "bold"))

    # ---- 按钮 ----
    style.configure("Primary.TButton", background=COLORS["primary"],
                    foreground="#FFFFFF", font=f["button"], padding=(18, 9),
                    borderwidth=0, relief="flat", focusthickness=0)
    style.map("Primary.TButton",
              background=[("active", COLORS["primary_dark"]),
                          ("pressed", COLORS["primary_dark"]),
                          ("disabled", "#A9B8D8")],
              foreground=[("disabled", "#F1F5F9")])

    style.configure("Secondary.TButton", background=COLORS["primary_soft"],
                    foreground=COLORS["primary_dark"], font=f["ui"],
                    padding=(14, 8), borderwidth=0, relief="flat")
    style.map("Secondary.TButton",
              background=[("active", "#D6E2FA"), ("disabled", "#EDF1F8")],
              foreground=[("disabled", "#9AA7BD")])

    style.configure("Ghost.TButton", background=card, foreground=muted,
                    font=f["small"], padding=(10, 5), borderwidth=0, relief="flat")
    style.map("Ghost.TButton",
              background=[("active", COLORS["row_alt"])],
              foreground=[("active", COLORS["primary_dark"])])

    # ---- 输入控件 ----
    style.configure("TEntry", fieldbackground="#FFFFFF", foreground=text,
                    bordercolor=border, lightcolor=border, darkcolor=border,
                    padding=(6, 5), insertcolor=text)
    style.map("TEntry", bordercolor=[("focus", COLORS["primary"])])
    style.configure("TCombobox", fieldbackground="#FFFFFF", foreground=text,
                    background="#FFFFFF", bordercolor=border,
                    lightcolor=border, darkcolor=border,
                    arrowcolor=muted, padding=(6, 4))
    style.map("TCombobox",
              bordercolor=[("focus", COLORS["primary"])],
              fieldbackground=[("readonly", "#FFFFFF")],
              foreground=[("readonly", text)])
    root.option_add("*TCombobox*Listbox.background", "#FFFFFF")
    root.option_add("*TCombobox*Listbox.foreground", text)
    root.option_add("*TCombobox*Listbox.selectBackground", COLORS["primary_soft"])
    root.option_add("*TCombobox*Listbox.selectForeground", COLORS["primary_dark"])

    style.configure("Card.TCheckbutton", background=card, foreground=text,
                    font=f["ui"], padding=(2, 3))
    style.map("Card.TCheckbutton", background=[("active", card)])

    # ---- 滚动条 ----
    style.configure("Vertical.TScrollbar", background=COLORS["status_bg"],
                    troughcolor=card, bordercolor=card, arrowcolor=muted,
                    relief="flat", width=10)
    style.map("Vertical.TScrollbar", background=[("active", "#CBD5E1")])

    # ---- 表格 ----
    style.configure("Treeview", background=card, fieldbackground=card,
                    foreground=text, rowheight=27, font=f["ui"],
                    bordercolor=border, borderwidth=0)
    style.configure("Treeview.Heading", background="#EFF3FA", foreground=muted,
                    font=f["small"], relief="flat", padding=(8, 7))
    style.map("Treeview", background=[("selected", COLORS["primary_soft"])],
              foreground=[("selected", COLORS["primary_dark"])])
    style.map("Treeview.Heading", background=[("active", "#E4EAF5")])

    # ---- 选项卡 ----
    style.configure("TNotebook", background=bg, borderwidth=0, tabmargins=(0, 0, 0, 0))
    style.configure("TNotebook.Tab", background="#E6EBF4", foreground=muted,
                    font=f["ui"], padding=(18, 8), borderwidth=0)
    style.map("TNotebook.Tab",
              background=[("selected", card)],
              foreground=[("selected", COLORS["primary_dark"])])

    # ---- 分隔线 ----
    style.configure("Card.TSeparator", background=border)

    return style


class Card(tk.Frame):
    """白底卡片: 1px 描边 + 标题 + 副标题 + 内容区 (self.content)。"""

    def __init__(self, master, title="", subtitle="", padding=14, **kw):
        super().__init__(master, bg=COLORS["border"], **kw)
        shell = tk.Frame(self, bg=COLORS["card"])
        shell.pack(fill=tk.BOTH, expand=True, padx=1, pady=1)
        inner = ttk.Frame(shell, style="Card.TFrame", padding=padding)
        inner.pack(fill=tk.BOTH, expand=True)

        if title:
            head = ttk.Frame(inner, style="Card.TFrame")
            head.pack(fill=tk.X, pady=(0, 10))
            accent = tk.Frame(head, bg=COLORS["primary"], width=4, height=16)
            accent.pack(side=tk.LEFT, padx=(0, 8))
            accent.pack_propagate(False)
            ttk.Label(head, text=title, style="CardTitle.TLabel").pack(side=tk.LEFT)
            if subtitle:
                ttk.Label(head, text="  " + subtitle, style="CardHint.TLabel").pack(
                    side=tk.LEFT, pady=(2, 0))

        self.content = ttk.Frame(inner, style="Card.TFrame")
        self.content.pack(fill=tk.BOTH, expand=True)


class LogView(tk.Frame):
    """深色只读日志区, 带滚动条与语义着色。"""

    TAGS = {
        "info": "#93C5FD",
        "success": "#4ADE80",
        "warn": "#FBBF24",
        "error": "#F87171",
        "muted": "#64748B",
    }

    def __init__(self, master, height=20, **kw):
        super().__init__(master, bg=COLORS["card"], **kw)
        f = fonts(master)
        self.text = tk.Text(
            self, height=height, wrap=tk.WORD, bg=COLORS["log_bg"],
            fg=COLORS["log_fg"], insertbackground=COLORS["log_fg"],
            selectbackground="#334155", relief=tk.FLAT, borderwidth=0,
            font=f["mono"], padx=12, pady=10, spacing1=1, spacing3=2,
        )
        scroll = ttk.Scrollbar(self, orient=tk.VERTICAL, command=self.text.yview)
        self.text.configure(yscrollcommand=scroll.set)
        self.text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.text.tag_configure("info", foreground=self.TAGS["info"])
        self.text.tag_configure("success", foreground=self.TAGS["success"])
        self.text.tag_configure("warn", foreground=self.TAGS["warn"])
        self.text.tag_configure("error", foreground=self.TAGS["error"])
        self.text.tag_configure("muted", foreground=self.TAGS["muted"])
        self.text.configure(state=tk.NORMAL)

    def append(self, text: str, tag=None) -> None:
        self.text.insert(tk.END, text + "\n", tag)
        self.text.see(tk.END)

    def clear(self) -> None:
        self.text.delete("1.0", tk.END)
