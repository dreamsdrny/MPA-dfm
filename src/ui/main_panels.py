"""主窗口的界面骨架: 页眉 / 状态栏 / 底部操作条 / 三张卡片.

这些函数只负责摆放控件并把引用挂到主窗口上, 业务动作由 MainWindow 提供
(浏览文件、运行流水线、打开输出目录等)。
"""
import tkinter as tk
from tkinter import ttk

from src.ui.theme import COLORS, Card, LogView
from src.ui.widgets import field_row, labeled_combo, labeled_entry


def build_header(root, win):
    """深色页眉: 左侧产品名与副标题, 右侧归属说明。"""
    header = tk.Frame(root, bg=COLORS["header"], height=72)
    header.pack(fill=tk.X)
    header.pack_propagate(False)

    brand = tk.Frame(header, bg=COLORS["header"])
    brand.pack(side=tk.LEFT, padx=24, pady=10)
    tk.Label(brand, text="Evan_BRD", bg=COLORS["header"],
             fg=COLORS["header_text"], font=win._fonts["title"]).pack(anchor=tk.W)
    tk.Label(brand, text="Cadence Allegro 板级分析 · Layout Checklist 自动填写",
             bg=COLORS["header"], fg=COLORS["header_muted"],
             font=win._fonts["subtitle"]).pack(anchor=tk.W, pady=(2, 0))

    meta = tk.Frame(header, bg=COLORS["header"])
    meta.pack(side=tk.RIGHT, padx=24, pady=10)
    tk.Label(meta, text="Evan 电子 · 内部工具", bg=COLORS["header"],
             fg=COLORS["header_text"], font=win._fonts["small"]).pack(anchor=tk.E)
    tk.Label(meta, text="支持 .brd (Allegro 17.x) / Excel Checklist",
             bg=COLORS["header"], fg=COLORS["header_muted"],
             font=win._fonts["small"]).pack(anchor=tk.E, pady=(2, 0))
    return header


def build_status_bar(root, win):
    """底部状态栏: 左侧状态文字, 右侧进度条 (按需显示)。"""
    status_bar = tk.Frame(root, bg=COLORS["status_bg"], height=36)
    status_bar.pack(fill=tk.X, side=tk.BOTTOM)
    status_bar.pack_propagate(False)
    tk.Label(status_bar, textvariable=win.status_var, bg=COLORS["status_bg"],
             fg=COLORS["muted"], font=win._fonts["small"],
             anchor=tk.W).pack(side=tk.LEFT, padx=16)
    win.progress = ttk.Progressbar(status_bar, mode="indeterminate", length=190)
    win.progress.pack(side=tk.RIGHT, padx=16, pady=7)
    win.progress.pack_forget()
    return status_bar


def build_footer(root, win):
    """底部操作条: 主操作常驻可见, 不随左侧卡片被挤压。"""
    bar = tk.Frame(root, bg=COLORS["border"])
    bar.pack(fill=tk.X, side=tk.BOTTOM)
    inner = tk.Frame(bar, bg=COLORS["card"])
    inner.pack(fill=tk.X, pady=(1, 0))
    box = ttk.Frame(inner, style="Card.TFrame", padding=(16, 10))
    box.pack(fill=tk.X)

    win.run_btn = ttk.Button(box, text="填写 Checklist 报告",
                             style="Primary.TButton", command=win._run_checklist)
    win.run_btn.pack(side=tk.LEFT)

    win.demo_btn = ttk.Button(box, text="演示运行", style="Secondary.TButton",
                              command=win._run_demo)
    win.demo_btn.pack(side=tk.LEFT, padx=(10, 0))
    win.analyze_btn = ttk.Button(box, text="BRD 分析与输出", style="Secondary.TButton",
                                 command=win._open_analyze)
    win.analyze_btn.pack(side=tk.LEFT, padx=(10, 0))
    win.open_btn = ttk.Button(box, text="打开输出目录", style="Secondary.TButton",
                              command=win._open_output)
    win.open_btn.pack(side=tk.LEFT, padx=(8, 0))

    ttk.Label(box, text="输出: <程序目录>/output", style="CardHint.TLabel").pack(
        side=tk.RIGHT)
    return bar


def build_input_card(parent, win):
    """① 输入文件: BRD / Checklist / 输出目录 / Net List。"""
    card = Card(parent, "① 输入文件", "BRD / Checklist / 输出目录")
    card.pack(fill=tk.X, pady=(0, 12))
    c = card.content
    field_row(c, "Allegro BRD 文件", win.brd_path, win._browse_brd)
    field_row(c, "Layout Checklist", win.checklist_path, win._browse_checklist)
    row = ttk.Frame(c, style="Card.TFrame")
    row.pack(fill=tk.X, pady=(0, 8))
    ttk.Button(row, text="检查模板", style="Ghost.TButton",
               command=win._inspect_template).pack(side=tk.LEFT)
    ttk.Label(row, text="先确认表头/列能识别, 再运行", style="CardHint.TLabel").pack(
        side=tk.LEFT, padx=(8, 0))
    field_row(c, "输出目录", win.output_dir, win._browse_output, width=34)
    ttk.Separator(c, orient=tk.HORIZONTAL, style="Card.TSeparator").pack(
        fill=tk.X, pady=(2, 10))
    field_row(c, "Net List JSON (可选，替代 BRD 网络表)", win.net_list_var,
              win._browse_nets, width=28)
    return card


def build_option_card(parent, win):
    """② 分析选项: 板厂 / 板型 / 能力等级 / 铜厚 / 填写人。"""
    card = Card(parent, "② 判定选项", "板厂 / 板型 / 等级 (Checklist 判定会引用)")
    card.pack(fill=tk.X, pady=(0, 12))
    c = card.content

    row1 = ttk.Frame(c, style="Card.TFrame")
    row1.pack(fill=tk.X)
    labeled_combo(row1, "板厂", win.factory, ["hongban", "sihui"],
                  width=10).bind("<<ComboboxSelected>>",
                                 lambda e: win._sync_level_combo())
    labeled_combo(row1, "板类型", win.board_type, ["rigid", "flex"], width=10).bind(
        "<<ComboboxSelected>>", lambda e: win._sync_level_combo())
    win.cap_combo = labeled_combo(row1, "能力等级", win.capability_level,
                                  ["standard", "automotive", "extreme"], width=11)

    row2 = ttk.Frame(c, style="Card.TFrame")
    row2.pack(fill=tk.X, pady=(12, 0))
    labeled_entry(row2, "铜厚 (um)", win.copper_um, width=7)
    labeled_entry(row2, "铜厚 (oz)", win.copper_oz, width=7)

    row3 = ttk.Frame(c, style="Card.TFrame")
    row3.pack(fill=tk.X, pady=(12, 0))
    ttk.Label(row3, text="Checklist 填写人", style="FieldLabel.TLabel").pack(side=tk.LEFT)
    ttk.Entry(row3, textvariable=win.checker_name, width=14).pack(side=tk.LEFT, padx=(8, 0))
    return card


def build_log_card(parent, win):
    """运行日志卡片: 深色日志区 + 清空 / 复制全部。"""
    card = Card(parent, "运行日志", "实时输出")
    card.pack(fill=tk.BOTH, expand=True)
    c = card.content

    bar = ttk.Frame(c, style="Card.TFrame")
    bar.pack(fill=tk.X, pady=(0, 8))
    ttk.Label(bar, text="生成过程、数据来源与覆盖率都会显示在这里",
              style="CardHint.TLabel").pack(side=tk.LEFT)
    ttk.Button(bar, text="清空", style="Ghost.TButton",
               command=win._clear_log).pack(side=tk.RIGHT)
    ttk.Button(bar, text="复制全部", style="Ghost.TButton",
               command=win._copy_log).pack(side=tk.RIGHT, padx=(0, 4))

    win.logview = LogView(c, height=24)
    win.logview.pack(fill=tk.BOTH, expand=True)
    win.log_text = win.logview.text
    return card
