"""Integrated native-plugin control; no detached Tk viewer or retained CAD dbids."""
import os
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from src.application.impedance import read_catalogue, read_results
from src.application.impedance import prepare_load_script, write_action, write_plugin
from src.ui.paths import app_base_dir, default_output_dir
from src.ui.responsive import FlowBar
from src.ui.tasks import TaskRunner


class ImpedancePanel(ttk.Frame):
    def __init__(self, parent):
        super().__init__(parent, padding=16)
        self.folder = tk.StringVar(master=self)
        self.layer = tk.StringVar(master=self)
        self.mode = tk.StringVar(master=self, value="几何筛选（不认定阻抗）")
        self.net = tk.StringVar(master=self)
        self.pair = tk.StringVar(master=self)
        self.width = tk.StringVar(master=self, value="5")
        self.spacing = tk.StringVar(master=self)
        self.impedance = tk.StringVar(master=self)
        self.tolerance = tk.StringVar(master=self, value="0.01")
        self.color = tk.StringVar(master=self)
        self.status = tk.StringVar(master=self, value="先生成插件，在有许可的 PCB Editor 当前 BRD 中运行 load_imp.scr；这里不会启动 Free Viewer。")
        self.catalogue = None
        self.color_ids = {}
        self.tasks = TaskRunner(self)
        self.columnconfigure(1, weight=1)
        self.rowconfigure(12, weight=1)
        ttk.Label(self, text="Allegro 原生阻抗/走线高亮 · IMP_HIGHLIGHT", style="CardTitle.TLabel").grid(row=0, column=0, columnspan=3, sticky="w", pady=8)
        bar = FlowBar(self)
        bar.grid(row=1, column=0, columnspan=3, sticky="ew")
        bar.add(ttk.Button(bar, text="① 生成原生插件", command=self._generate))
        bar.add(ttk.Button(bar, text="选择插件目录", command=self._browse))
        bar.add(ttk.Button(bar, text="准备加载脚本", command=self._prepare))
        bar.add(ttk.Button(bar, text="打开插件目录", command=self._open_folder))
        bar.add(ttk.Button(bar, text="② 导入当前 Allegro 板目录", command=self._load))
        bar.add(ttk.Button(bar, text="③ 生成高亮执行脚本", command=self._apply))
        bar.add(ttk.Button(bar, text="④ 读取原生高亮结果", command=self._results))
        ttk.Entry(self, textvariable=self.folder).grid(row=2, column=0, columnspan=3, sticky="ew", pady=6)
        fields = [("目标铜层", self.layer, "layer"), ("识别方式", self.mode, "mode"),
                  ("Net（精确名称，可空）", self.net, "net"), ("Differential Pair（可空）", self.pair, "pair"),
                  ("实际线宽 mil（空白不限）", self.width, "width"), ("实际线距/对内间距 mil（可空）", self.spacing, "spacing"),
                  ("阻抗目标 ohm（约束值，可空）", self.impedance, "impedance"),
                  ("线宽/线距容差 mil", self.tolerance, "tolerance"), ("Allegro 调色板", self.color, "color")]
        self.combos = {}
        for row, (label, var, name) in enumerate(fields, 3):
            ttk.Label(self, text=label).grid(row=row, column=0, sticky="w", padx=(0, 16), pady=3)
            if name in ("layer", "mode", "net", "pair", "color"):
                widget = ttk.Combobox(self, textvariable=var, state="readonly", width=48)
                self.combos[name] = widget
            else:
                widget = ttk.Entry(self, textvariable=var)
            widget.grid(row=row, column=1, columnspan=2, sticky="ew", pady=3)
        self.combos["mode"].configure(values=("几何筛选（不认定阻抗）", "阻抗约束网络", "数据库差分对"))
        self.swatch = tk.Label(self, text="CAD 色块", width=9, relief="solid", borderwidth=1)
        self.swatch.grid(row=11, column=3, padx=6)
        self.color.trace_add("write", lambda *_: self._update_swatch())
        self.rules = ttk.Treeview(self, columns=("net", "layer", "rule"), show="headings", height=10)
        for name, title, width in (("net", "Net", 160), ("layer", "层", 100), ("rule", "约束证据 / Physical / DP gap", 700)):
            self.rules.heading(name, text=title)
            self.rules.column(name, width=width)
        self.rules.grid(row=12, column=0, columnspan=3, sticky="nsew", pady=8)
        self.rule_scroll = ttk.Scrollbar(self, orient="vertical", command=self.rules.yview)
        self.rule_scroll.grid(row=12, column=3, sticky="ns")
        self.rules.configure(yscrollcommand=self.rule_scroll.set)
        ttk.Label(self, textvariable=self.status, wraplength=1000).grid(row=13, column=0, columnspan=3, sticky="ew", pady=8)
        ttk.Label(self, text="原生面板可直接选层和输入线宽/间距；主程序生成的执行脚本须在同一已加载插件的 PCB Editor 中运行。\n"
                  "差分模式仅测真实配对的另一条腿；几何模式不等于阻抗认证。阻抗为 CM 目标，不是场求解实测。",
                  wraplength=1000).grid(row=14, column=0, columnspan=3, sticky="ew")
        self.layer.trace_add("write", lambda *_: self._show_rules())
        self.folder.trace_add("write", lambda *_: self._invalidate())
        bundled = Path(app_base_dir()) / "IMP_HIGHLIGHT"
        if (bundled / "session.json").is_file():
            self.folder.set(str(bundled))
            self.status.set("已选交付包中的插件：先点「准备加载脚本」，再在当前 Allegro PCB Editor 的 File > Script 运行 load_imp.scr。")

    def _invalidate(self):
        self._revision = getattr(self, "_revision", 0) + 1
        self.catalogue = None
        self.color_ids = {}
        for name in ("layer", "net", "pair", "color"):
            self.combos[name].configure(values=())
        self.layer.set("")
        self.net.set("")
        self.pair.set("")
        self.color.set("")
        children = self.rules.get_children()
        if children:
            self.rules.delete(*children)
        self.status.set("插件目录已变化，请准备加载脚本并重新导入 Allegro 当前板目录。")

    def _update_swatch(self):
        if not self.catalogue or self.color.get() not in self.color_ids:
            self.swatch.configure(bg="#888888")
            return
        index = self.color_ids[self.color.get()]
        for row in self.catalogue["colors"]:
            if int(row["NAME"]) == index:
                rgb = [int(v) for v in row["VALUE"].split(",")]
                if len(rgb) == 3 and all(0 <= v <= 255 for v in rgb):
                    self.swatch.configure(bg="#" + "".join(f"{v:02x}" for v in rgb))

    def _generate(self):
        parent = filedialog.askdirectory(parent=self.winfo_toplevel(), initialdir=default_output_dir(), title="选择新建插件目录的父目录")
        if not parent:
            return
        import uuid
        try:
            folder = write_plugin(Path(parent) / ("IMP_HIGHLIGHT_" + uuid.uuid4().hex[:8]))
            self.folder.set(str(folder))
            self.catalogue = None
            self.status.set(f"在当前 Allegro PCB Editor 的 File > Script 运行 {folder / 'load_imp.scr'}；结束原生命令后导入当前板目录。")
            from src.application.impedance import quote_skill
            self._copy_commands(f'skill load({quote_skill(folder.as_posix() + "/imp_highlight.il")})\n'
                                f'skill imp_setup({quote_skill(folder.as_posix())} "{self._token(folder)}")\nIMP_HIGHLIGHT')
        except (OSError, ValueError) as exc:
            self._error(exc)

    @staticmethod
    def _token(folder):
        from src.application.impedance import session_token
        return session_token(folder)

    def _browse(self):
        path = filedialog.askdirectory(parent=self.winfo_toplevel(), title="选择已生成的 IMP_HIGHLIGHT 插件目录")
        if path:
            self.folder.set(path)
            self.catalogue = None

    def _prepare(self):
        try:
            path = prepare_load_script(self.folder.get().strip())
            from src.application.impedance import load_script_text
            self._copy_commands(load_script_text(path))
            self.status.set(f"已按当前目录修复加载脚本并复制命令。在 Allegro 打开 BRD，通过 File > Script 运行：{path}")
        except (OSError, ValueError) as exc:
            self._error(exc)

    def _open_folder(self):
        try:
            folder = Path(self.folder.get().strip())
            if not self.folder.get().strip() or not folder.is_dir():
                raise ValueError("请选择有效插件目录")
            os.startfile(folder)
        except (OSError, ValueError) as exc:
            self._error(exc)

    def _load(self):
        folder = self.folder.get().strip()
        revision = getattr(self, "_revision", 0)
        self.catalogue = None
        self._show_rules()
        def done(catalogue):
            if revision != getattr(self, "_revision", 0) or folder != self.folder.get().strip():
                return
            self.catalogue = catalogue
            self.net.set("")
            self.pair.set("")
            self.combos["layer"].configure(values=[r["NAME"] for r in catalogue["layers"]])
            self.combos["net"].configure(values=[""] + [r["NAME"] for r in catalogue["nets"]])
            self.combos["pair"].configure(values=[""] + [r["NAME"] for r in catalogue["pairs"]])
            self.color_ids = {f"{r['NAME']} · RGB({r['VALUE']})": int(r["NAME"]) for r in catalogue["colors"]}
            self.combos["color"].configure(values=list(self.color_ids))
            if catalogue["layers"]:
                self.layer.set(catalogue["layers"][0]["NAME"])
            if self.color_ids:
                self.color.set(next(iter(self.color_ids)))
            self._show_rules()
            selected_count = sum(row["VALUE"] == self.layer.get() for row in catalogue["rules"])
            self._revision = getattr(self, "_revision", 0) + 1
            self.status.set(f"当前导出板：{catalogue['board']}；{len(catalogue['layers'])} 铜层 / {len(catalogue['nets'])} Net / {len(catalogue['pairs'])} 差分对。"
                            + (f" 当前层 {selected_count} 条约束只显示前 2000 条。" if selected_count > 2000 else "")
                            + (" 无阻抗约束记录，不能确认阻抗。" if not catalogue["rules"] else ""))
        def failed(message):
            if revision == getattr(self, "_revision", 0):
                self._error(message)
        self.tasks.submit("catalogue", lambda: read_catalogue(folder), done, failed)

    def _show_rules(self):
        children = self.rules.get_children()
        if children:
            self.rules.delete(*children)
        if self.catalogue:
            rows = [r for r in self.catalogue["rules"] if r["VALUE"] == self.layer.get()]
            for row in rows[:2000]:
                self.rules.insert("", "end", values=(row["NAME"], row["VALUE"], row["DETAIL"]))
            if len(rows) > 2000:
                self.status.set(f"当前层有 {len(rows)} 条约束记录，列表只显示前 2000 条；未显示部分不代表不存在。")

    def _apply(self):
        try:
            if not self.catalogue or self.layer.get() not in {r["NAME"] for r in self.catalogue["layers"]}:
                raise ValueError("请先导入当前 Allegro 的真实铜层/Net/约束目录")
            current = read_catalogue(self.folder.get().strip())
            if (current.get("catalog_sha256") != self.catalogue.get("catalog_sha256")
                    or current.get("session_token") != self.catalogue.get("session_token")
                    or current["board"] != self.catalogue["board"]):
                self._invalidate()
                raise ValueError("Allegro 板目录已变化，请重新导入")
            if self.color.get() not in self.color_ids:
                raise ValueError("请选择 Allegro 实际调色板颜色")
            mode = {"几何筛选（不认定阻抗）": "geometry", "阻抗约束网络": "impedance", "数据库差分对": "pair"}[self.mode.get()]
            if mode == "geometry" and self.impedance.get().strip():
                raise ValueError("阻抗目标必须使用阻抗约束或差分对识别模式")
            path = write_action(self.folder.get(), layer=self.layer.get(), width=self.width.get(), spacing=self.spacing.get(),
                tolerance=self.tolerance.get(), mode=mode, net=self.net.get(), pair=self.pair.get(),
                impedance=self.impedance.get(), color=self.color_ids[self.color.get()], board=self.catalogue["board"])
            self._copy_commands(str(path))
            self.status.set(f"请结束旧原生命令，在同一 PCB Editor 通过 File > Script 运行 {path}。"
                            "它会重新读取当前层/约束并打开原生高亮面板；保持面板打开查看高亮，关闭会恢复显示。")
        except (OSError, ValueError, KeyError) as exc:
            self._error(exc)

    def _copy_commands(self, text):
        self.clipboard_clear()
        self.clipboard_append(text)

    def _results(self):
        folder = self.folder.get().strip()
        revision = getattr(self, "_revision", 0)
        if not self.catalogue:
            self._error("请先导入当前 Allegro 板目录，再读取原生结果")
            return
        expected_catalogue = self.catalogue.get("catalog_sha256")
        expected_token = self.catalogue.get("session_token")
        expected_board = self.catalogue["board"]
        expected_layer = self.layer.get()
        def work():
            before = read_catalogue(folder)
            rows = read_results(folder)
            after = read_catalogue(folder)
            if (before.get("catalog_sha256") != expected_catalogue or after.get("catalog_sha256") != expected_catalogue
                    or before.get("session_token") != expected_token or after.get("session_token") != expected_token):
                raise ValueError("Allegro 板目录已变化，请重新导入后再读取结果")
            if any(row.get("BRD") != expected_board for row in rows):
                raise ValueError("结果来自另一份 BRD，请重新执行当前板高亮")
            if any(row.get("LAYER") != "ETCH/" + expected_layer for row in rows):
                raise ValueError("原生结果不属于当前所选层，请重新选择或执行高亮")
            return rows
        def done(rows):
            if revision != getattr(self, "_revision", 0) or folder != self.folder.get().strip():
                return
            if (not self.catalogue or self.catalogue.get("catalog_sha256") != expected_catalogue
                    or self.catalogue.get("session_token") != expected_token or self.layer.get() != expected_layer):
                self.status.set("结果读取期间板目录或所选层已变化，已丢弃旧结果。")
                return
            children = self.rules.get_children()
            if children:
                self.rules.delete(*children)
            for row in rows[:2000]:
                self.rules.insert("", "end", values=(row["NET"], row["LAYER"],
                    f"Width={row['WIDTH_MIL']} mil; Spacing={row['SPACING_MIL']} mil; Pair={row['PAIR']}; "
                    + row["IMPEDANCE_RULE"] + ";Physical=" + row["PHYSICAL_CSET"]))
            self.status.set(f"读取原生执行结果 {len(rows)} 段（列表最多显示 2000），此文件记录的是最后一次原生命令，换板/编辑后需重新执行。")
        def failed(message):
            if revision == getattr(self, "_revision", 0) and self.layer.get() == expected_layer:
                self._error(message)
        self.tasks.submit("results", work, done, failed)

    def _error(self, message):
        self.status.set(str(message))
        messagebox.showerror("IMP_HIGHLIGHT", str(message), parent=self.winfo_toplevel())
