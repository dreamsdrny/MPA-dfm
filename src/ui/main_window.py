"""主窗口 (MainWindow): 组装界面、管理生命周期、调度后台任务.

分工:
    界面骨架      main_panels.py  (页眉/状态栏/底部条/三张卡片)
    填写 Checklist src/checklist_pipeline.py (BRD -> 填写 -> 报告, 不碰 tkinter)
    偏好记忆      prefs.py

DFM 分析在独立程序 Evan_DFM.exe 里 (evan_dfm.py + dfm_dialog.py), 本窗口不再
打开它、也不再从本窗口传 BRD 给它 —— 两个程序各自导入自己的 BRD。

本文件只做"把它们连起来": 线程 + 队列 + 状态栏, 所有 Tk 调用都留在主线程。
"""
import os
import threading
import tkinter as tk
import traceback
from tkinter import filedialog, messagebox, ttk

from src import checklist_pipeline
from src.checklist_template import read_template
from src.ui.main_panels import (build_footer, build_header, build_input_card,
                               build_log_card, build_option_card,
                               build_status_bar)
from src.ui.paths import app_base_dir, default_output_dir
from src.ui.prefs import load_prefs, save_prefs
from src.ui.theme import COLORS, apply_theme, enable_dpi_awareness, fonts


class MainWindow:
    """BRD 分析主窗口。master 为空时创建并接管主循环 (独立运行)。"""

    def __init__(self, master=None):
        self._owns_root = master is None
        enable_dpi_awareness()
        if master is None:
            self.root = tk.Tk()
        else:
            self.root = tk.Toplevel(master)
        self.root.title("Evan_BRD - 分析BRD (Cadence Allegro)")
        self.root.geometry("1200x800")
        self.root.minsize(1060, 720)
        self.root.configure(bg=COLORS["bg"])
        self._fonts = fonts(self.root)
        apply_theme(self.root)
        # 每次启动重写界面日志, 便于用户只回传"这一次"的记录
        self._ui_log_path = os.path.join(app_base_dir(), "Evan_BRD_ui.log")
        try:
            with open(self._ui_log_path, "w", encoding="utf-8") as f:
                f.write("")
        except OSError:
            self._ui_log_path = None
        # 窗口版没有控制台: 界面回调里的异常写到日志区 + crash.log
        self.root.report_callback_exception = self._on_callback_error

        # ---- 输入变量 ----
        self.brd_path = tk.StringVar()
        self.checklist_path = tk.StringVar()
        self.output_dir = tk.StringVar()
        self.factory = tk.StringVar(value="hongban")
        self.board_type = tk.StringVar(value="rigid")
        self.capability_level = tk.StringVar(value="standard")
        self.copper_um = tk.StringVar(value="35")
        self.copper_oz = tk.StringVar(value="1.0")
        self.net_list_var = tk.StringVar()
        self.autofill_var = tk.BooleanVar(value=True)
        self.checker_name = tk.StringVar(value="BRD-AI")
        self.status_var = tk.StringVar(value="就绪")

        self._build_ui()

    # ------------------------------------------------------------ 界面
    def _build_ui(self):
        build_header(self.root, self)
        build_status_bar(self.root, self)
        build_footer(self.root, self)

        body = ttk.Frame(self.root, style="TFrame", padding=(16, 14, 16, 12))
        body.pack(fill=tk.BOTH, expand=True)
        body.columnconfigure(0, weight=0, minsize=452)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)

        left = ttk.Frame(body, style="TFrame")
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 14))
        right = ttk.Frame(body, style="TFrame")
        right.grid(row=0, column=1, sticky="nsew")

        build_input_card(left, self)
        build_option_card(left, self)
        build_log_card(right, self)

        self.output_dir.set(default_output_dir())
        self._restore()
        self.root.protocol("WM_DELETE_WINDOW", self._close)
        self._sync_level_combo()
        # 启动时把恢复到的状态写进日志 (打包版排查"为什么路径是空的"最有用)
        self._log("[启动] 恢复上次设置: "
                  f"BRD={self.brd_path.get() or '(未选)'} | "
                  f"Checklist={self.checklist_path.get() or '(未选)'} | "
                  f"输出={self.output_dir.get() or '(未选)'}")

    def _set_busy(self, busy: bool):
        state = tk.DISABLED if busy else tk.NORMAL
        for btn in (self.run_btn, self.demo_btn, self.open_btn, self.analyze_btn):
            btn.config(state=state)
        if busy:
            self.progress.pack(side=tk.RIGHT, padx=16, pady=7)
            self.progress.start(12)
        else:
            self.progress.stop()
            self.progress.pack_forget()

    # ------------------------------------------------------------ 生命周期
    def _close(self):
        self._remember()
        try:
            self.root.destroy()
        except tk.TclError:
            pass

    def _remember(self, event=None):
        """关闭窗口时记住路径与选项, 下次打开直接续上 (不覆盖界面二的 dfm_brd)。"""
        data = load_prefs()
        data.update({
            "brd": self.brd_path.get().strip(),
            "checklist": self.checklist_path.get().strip(),
            "output": self.output_dir.get().strip(),
            "net_list": self.net_list_var.get().strip(),
            "factory": self.factory.get(),
            "board_type": self.board_type.get(),
            "level": self.capability_level.get(),
            "copper_um": self.copper_um.get(),
            "copper_oz": self.copper_oz.get(),
            "checker": self.checker_name.get(),
            "autofill": bool(self.autofill_var.get()),
        })
        save_prefs(data)

    def _restore(self):
        prefs = load_prefs()
        if not prefs:
            return
        for key, var in (("brd", self.brd_path), ("checklist", self.checklist_path),
                         ("output", self.output_dir), ("net_list", self.net_list_var),
                         ("factory", self.factory), ("board_type", self.board_type),
                         ("level", self.capability_level), ("copper_um", self.copper_um),
                         ("copper_oz", self.copper_oz), ("checker", self.checker_name)):
            val = prefs.get(key)
            if isinstance(val, str) and val:
                var.set(val)
        if isinstance(prefs.get("autofill"), bool):
            self.autofill_var.set(prefs["autofill"])

    # ------------------------------------------------------------ 日志
    def _log(self, text, tag=None):
        self.logview.append(text, tag)
        self._log_to_file(text, tag)
        try:
            self.root.update_idletasks()
        except tk.TclError:
            pass

    def _log_to_file(self, text, tag=None):
        """界面日志同时落盘: 打包版没有控制台, 用户回传这个文件就能复现问题。"""
        if self._ui_log_path is None:
            return
        try:
            with open(self._ui_log_path, "a", encoding="utf-8") as f:
                f.write(f"[{tag or 'info'}] {text}\n")
        except OSError:
            pass

    def _post_log(self, text, tag=None):
        """线程安全的日志: 后台线程调用, 投递回主线程执行。"""
        try:
            self.root.after(0, lambda: self._log(text, tag))
        except (RuntimeError, tk.TclError):
            pass

    def _clear_log(self):
        self.logview.clear()

    def _copy_log(self):
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(self.log_text.get("1.0", tk.END).rstrip())
            self.status_var.set("日志已复制到剪贴板")
        except tk.TclError:
            pass

    # ------------------------------------------------------------ 浏览
    def _browse_brd(self):
        path = filedialog.askopenfilename(
            title="Select Allegro BRD File",
            filetypes=[("BRD files", "*.brd"), ("All files", "*.*")])
        if path:
            self.brd_path.set(path)

    def _browse_checklist(self):
        path = filedialog.askopenfilename(
            title="Select Layout Checklist Excel",
            filetypes=[("Excel files", "*.xlsx"), ("All files", "*.*")])
        if path:
            self.checklist_path.set(path)

    def _browse_output(self):
        path = filedialog.askdirectory(title="Select Output Directory")
        if path:
            self.output_dir.set(path)

    def _browse_nets(self):
        path = filedialog.askopenfilename(
            title="Select Net List JSON",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")])
        if path:
            self.net_list_var.set(path)

    def _sync_level_combo(self):
        from src.dfm_engine import list_levels, normalize_level

        levels = list_levels(self.board_type.get(), factory=self.factory.get()) or ["standard"]
        self.cap_combo.configure(values=levels)
        cur = normalize_level(self.capability_level.get())
        if cur not in levels:
            cur = levels[0]
        self.capability_level.set(cur)

    def _open_output(self):
        out = self.output_dir.get()
        if out and os.path.exists(out):
            os.startfile(out)
        else:
            messagebox.showwarning("Warning", "Output directory does not exist. "
                                              "Run generation first.")

    def _open_analyze(self):
        """打开「BRD 分析与输出」对话框 (8 大类能力)。"""
        from src.ui.analyze_dialog import open_analyze

        brd = self.brd_path.get().strip()
        if not brd:
            messagebox.showwarning("提示", "请先在「① 输入文件」里选择 Allegro BRD 文件")
            return
        if not os.path.exists(brd):
            messagebox.showerror("提示", f"文件不存在:\n{brd}")
            return
        try:
            open_analyze(self.root, brd_path=brd,
                         out_dir=self.output_dir.get().strip(),
                         log=self._log)
        except Exception as exc:  # noqa: BLE001
            self._log(f"[ERROR] 打开分析对话框失败: {exc}", "error")

    # ------------------------------------------------------------ 主流程: 填写 Checklist
    def _run_demo(self):
        self.brd_path.set("")
        self.checklist_path.set("")
        self._run_checklist(use_demo=True)

    def _run_checklist(self, use_demo=False):
        if not use_demo:
            if not self.checklist_path.get().strip():
                messagebox.showwarning(
                    "提示", "请选择要填写的 Checklist (.xlsx) 模板")
                return
            if not self.brd_path.get().strip() and not self.net_list_var.get().strip():
                messagebox.showwarning(
                    "提示", "请选择 BRD 文件 (或 Net List JSON)")
                return

        self._set_busy(True)
        self.logview.clear()
        self.status_var.set("正在填写 Checklist…")
        threading.Thread(target=self._execute_checklist, args=(use_demo,),
                         daemon=True).start()

    def _execute_checklist(self, use_demo):
        try:
            result = checklist_pipeline.run(
                checklist_path=self.checklist_path.get().strip(),
                output_dir=self.output_dir.get(),
                brd_path=self.brd_path.get().strip(),
                nets_path=self.net_list_var.get().strip(),
                factory=self.factory.get(),
                board_type=self.board_type.get(),
                level=self.capability_level.get(),
                copper_um=self.copper_um.get(),
                copper_oz=self.copper_oz.get(),
                checker=self.checker_name.get().strip(),
                demo=use_demo,
                log=self._post_log,
            )
            stats = result["stats"]
            text = (f"Checklist 完成: PASS {stats['auto_pass']} / "
                    f"VERIFY {stats['auto_verify']} / FAIL {stats['auto_fail']} / "
                    f"需人工 {stats['manual']} / N/A {stats['na']}")
            self.root.after(0, lambda: self.status_var.set(text))
            self._last_result = result
        except Exception as exc:  # noqa: BLE001
            message = str(exc)          # 先取出文本: except 结束后 exc 会被删除
            detail = traceback.format_exc()
            self.root.after(0, lambda: self._log(f"[ERROR] {message}", "error"))
            self.root.after(0, lambda: self._log(detail, "error"))
            self.root.after(0, lambda: self.status_var.set("Error"))
        finally:
            self._finish()

    def _inspect_template(self):
        """只读模板结构, 确认表头/列能识别 (更新模板后先点这里)。"""
        path = self.checklist_path.get().strip()
        if not path:
            messagebox.showwarning("提示", "请先选择 Checklist 模板")
            return
        if not os.path.exists(path):
            messagebox.showerror("提示", f"文件不存在:\n{path}")
            return
        try:
            info = read_template(path)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("模板检查失败", str(exc))
            return

        names = {"category": "类别", "subcategory": "子类", "no": "编号",
                 "grade": "级别", "items": "条目", "standard": "符合标准",
                 "note": "原因", "check": "检查", "approved": "签核"}
        cols = "，".join(f"{names.get(k, k)}={v}" for k, v in sorted(
            info["columns"].items(), key=lambda kv: kv[1]))
        lines = [
            f"工作表: {info['sheet']}",
            f"表头行: 第 {info['header_row']} 行",
            f"识别方式: {info['method']}",
            f"列位置: {cols}",
            f"检查项: {info['count']} 项",
            f"规则库: {info['rule_count']} 条 ({info['config_path'] or '内置默认'})",
        ]
        if info["warnings"]:
            lines.append("")
            lines.append("警告:")
            lines.extend(f"· {w}" for w in info["warnings"])
        else:
            lines.append("")
            lines.append("模板结构正常, 可以直接运行。")
        self._log(f"[模板] {path}")
        for line in lines:
            self._log(f"    {line}")
        messagebox.showinfo("模板检查", "\n".join(lines))

    def _on_callback_error(self, exc_type, exc, tb):
        text = "".join(traceback.format_exception(exc_type, exc, tb))
        try:
            self._log("[ERROR] 界面回调异常: " + str(exc), "error")
            self._log(text, "error")
        except Exception:  # noqa: BLE001
            pass
        try:
            from evan_brd import log_crash
            log_crash("tk-callback", text)
        except Exception:  # noqa: BLE001
            pass

    def _finish(self):
        """后台线程收尾: 控件操作一律回主线程执行 (Tk 非线程安全)。"""
        try:
            self.root.after(0, self._finish_ui)
        except RuntimeError:
            pass

    def _finish_ui(self):
        self._set_busy(False)

    # ------------------------------------------------------------ 运行
    def run(self):
        if self._owns_root:
            self.root.mainloop()
