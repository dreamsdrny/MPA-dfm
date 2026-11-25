"""Evan_DFM: 界面二 —— DFM 分析 (独立程序).

界面一 (Evan_BRD.exe) 只负责 Checklist 填写; DFM 分析在这里, 自己导入 BRD,
不从界面一取任何路径或参数。

用法:
    Evan_DFM.exe                打开 Gerber 工作台，不自动带出历史 BRD
    Evan_DFM.exe board.brd      打开窗口并直接读这块板
"""
import os
import sys
import tkinter as tk
import traceback


def _crash_path() -> str:
    """崩溃日志写到程序旁边 (打包后是 exe 目录, 不是临时解压目录)。"""
    if getattr(sys, "frozen", False):
        base = os.path.dirname(sys.executable)
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, "Evan_DFM_crash.log")


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    brd = next((a for a in argv if a.lower().endswith(".brd")), "")
    gerber = ""
    if "--gerber" in argv:
        index = argv.index("--gerber")
        if index + 1 >= len(argv):
            raise ValueError("--gerber 需要文件、目录或压缩包路径")
        gerber = argv[index + 1]
    license_path = None
    if "--license" in argv:
        index = argv.index("--license")
        if index + 1 < len(argv):
            license_path = argv[index + 1]

    if "--check-pcb-ui" in argv:
        import json
        from pathlib import Path
        from src.bootstrap import prepare_startup
        index = argv.index("--check-pcb-ui")
        if index + 1 >= len(argv):
            raise ValueError("--check-pcb-ui 需要指定结果 JSON 路径")
        output = Path(argv[index + 1])
        output.parent.mkdir(parents=True, exist_ok=True)
        result = {"valid": False}
        try:
            prepare_startup(license_path=license_path)
            from src.application.pcb_diagnostic import check_packaged_pcb
            board_type = argv[argv.index("--board-type") + 1] if "--board-type" in argv else "rigid"
            result = check_packaged_pcb(output, board=brd, gerber=gerber,
                                        board_type=board_type, native="--native" in argv)
        except Exception as exc:
            result["error"] = str(exc)
            from src.infrastructure.diagnostics import log_exception
            log_exception("packaged PCB acceptance", type(exc), exc, exc.__traceback__)
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        return 0 if result["valid"] else 1

    # 管理员自动验收出口：仍执行完整授权校验，不创建业务界面或绕过许可。
    if "--check-deployment" in argv:
        import json
        from pathlib import Path
        from src.bootstrap import prepare_startup
        index = argv.index("--check-deployment")
        if index + 1 >= len(argv):
            raise ValueError("--check-deployment 需要指定结果 JSON 路径")
        try:
            context = prepare_startup(license_path=license_path)
            status = context.deployment
            result = {"valid": True, "app_id": status["app_id"],
                      "starts": status["license"]["starts"], "expires": status["license"]["expires"],
                      "dependencies": list(context.dependencies),
                      "max_workers": context.config.max_workers}
        except (OSError, ValueError) as exc:
            result = {"valid": False, "error": str(exc)}
        Path(argv[index + 1]).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        return 0 if result["valid"] else 1

    # 发布验收：验证授权后实际创建主窗口与走线窗口；不打开 BRD 或 CAD。
    if "--check-trace-ui" in argv:
        import json
        from pathlib import Path
        from src.bootstrap import prepare_startup
        index = argv.index("--check-trace-ui")
        if index + 1 >= len(argv):
            raise ValueError("--check-trace-ui 需要指定结果 JSON 路径")
        result = {"valid": False}
        root = None
        try:
            prepare_startup(license_path=license_path)
            from src.ui.dfm_dialog import DFMDialog
            root = tk.Tk()
            root.withdraw()
            dialog = DFMDialog(root, standalone=False)
            dialog.trace_filter_button.invoke()
            root.update()
            trace = dialog.impedance_panel
            if not trace.winfo_exists() or dialog.workbench.select() != str(trace):
                raise RuntimeError(f"主窗口未能打开原生阻抗高亮页签：selected={dialog.workbench.select()!r} expected={str(trace)!r} "
                                   f"exists={trace.winfo_exists()} button={dialog.trace_filter_button.cget('state')}")
            import tempfile
            from src.impedance_plugin import write_plugin
            approved = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "Temp" / "opencode"
            approved.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix="imp-packaged-", dir=approved) as temp:
                bundle = write_plugin(temp)
                if "IMP_HIGHLIGHT" not in (bundle / "imp_highlight.il").read_text(encoding="utf-8"):
                    raise RuntimeError("打包的原生 SKILL 资源缺失")
            result = {"valid": True, "button": str(dialog.trace_filter_button.cget("text")),
                      "trace_title": "Allegro 阻抗 / 走线高亮", "native_brd_tested": False,
                      "integrated_tab": True, "native_plugin_resources": True}
            dialog._close()
        except Exception as exc:  # noqa: BLE001  发布诊断保留具体失败而不是伪造成功
            result["error"] = str(exc)
        finally:
            if root:
                root.destroy()
        Path(argv[index + 1]).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        return 0 if result["valid"] else 1

    if "--check-gerber-ui" in argv:
        import json
        from pathlib import Path
        from src.bootstrap import prepare_startup
        index = argv.index("--check-gerber-ui")
        if index + 1 >= len(argv):
            raise ValueError("--check-gerber-ui 需要指定结果 JSON 路径")
        output = Path(argv[index + 1])
        output.parent.mkdir(parents=True, exist_ok=True)
        result = {"valid": False}
        root = dialog = None
        try:
            prepare_startup(license_path=license_path)
            from src.ui.dfm_dialog import DFMDialog
            from src.application.gerber_diagnostic import check_packaged_gerber
            root = tk.Tk()
            root.withdraw()
            dialog = DFMDialog(root, standalone=True)
            root.update_idletasks()
            if not dialog._gerber_active() or dialog.advanced_frame.winfo_manager():
                raise RuntimeError("Gerber 默认页签或高级区域状态不正确")
            result.update(check_packaged_gerber(output))
            if result["layers"] != 6 or not result["problem_locations"] or result["releasable"]:
                raise RuntimeError("Gerber 发布验收结果不符合预期")
            result.update(valid=True, gerber_default_tab=True, advanced_controls_hidden=True)
        except Exception as exc:  # noqa: BLE001 preserve real packaged errors
            result["error"] = str(exc)
        finally:
            if dialog:
                dialog._finish_close()
            elif root:
                root.destroy()
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        return 0 if result["valid"] else 1

    # Packaging diagnostic: real SMT tab, bundled profiles, synthetic geometry and report.
    # It still requires a valid license; it never imports/modifies a user's board.
    if "--check-smt-ui" in argv:
        import json
        from pathlib import Path
        from src.bootstrap import prepare_startup
        index = argv.index("--check-smt-ui")
        if index + 1 >= len(argv):
            raise ValueError("--check-smt-ui 需要指定结果 JSON 路径")
        output = Path(argv[index + 1])
        result = {"valid": False, "synthetic_data": True, "native_brd_tested": False}
        root = None
        dialog = None
        try:
            prepare_startup(license_path=license_path)
            from src.ui.dfm_dialog import DFMDialog
            from src.application.smt_analysis import analyze_smt_workflow, design_facts
            from src.smt_geometry import analyze_objects, apply_object_results
            from src.smt_engine import export_smt_report
            root = tk.Tk()
            root.withdraw()
            dialog = DFMDialog(root, standalone=False)
            panel = dialog.smt
            doc = panel.doc
            if not doc or doc["meta"]["factory"] != "Lijing" or not panel.baseline:
                raise RuntimeError("未能加载打包后的立景能力和内部基线")
            facts = design_facts(dict(panel_size=[250, 300], board_thickness=1.6), panel.fields)
            analysis = analyze_smt_workflow(doc, "automotive", facts, baseline=panel.baseline)
            data = dict(_source="合成打包验收对象", board_outline=[[0,0],[20,0],[20,20],[0,20]],
                        complete={"components": True, "vcut_lines": True},
                        components=[dict(refdes="R1", side="TOP", category="chip_0402", boundary="body",
                                         outline=[[5,5],[6,5],[6,6],[5,6]]),
                                    dict(refdes="R2", side="TOP", category="chip_0805", boundary="body",
                                         outline=[[6.35,5],[7.35,5],[7.35,6],[6.35,6]])])
            evidence, errors = analyze_objects(doc, "automotive", data)
            apply_object_results(analysis, evidence, errors)
            rows = {r["field"]: r for r in analysis["items"]}
            if rows["panel_size_x"]["actual"] != 300 or rows["chip_0402_spacing"]["status"] != "fail":
                raise RuntimeError("打包后的尺寸共享或混排器件检测不符合预期")
            panel.last_result = analysis
            panel._render_result(analysis, panel._revision)
            # Process layout only: the normal BRD guard must invalidate synthetic
            # results on an unimported board if periodic callbacks are run.
            root.update_idletasks()
            report = output.with_name("smt_packaged.xlsx")
            export_smt_report(analysis, report)
            from openpyxl import load_workbook
            workbook = load_workbook(report, read_only=True)
            sheets = list(workbook.sheetnames)
            workbook.close()
            result.update(valid=True, factory=doc["meta"]["factory"],
                          numeric_rules=len(doc["profiles"]["automotive"]["rules"]),
                          manual_conditions=len(panel.review_tree.get_children()),
                          rendered_items=len(panel.tree.get_children()),
                          baseline_rows=len(panel.baseline_tree.get_children()),
                          mixed_spacing_status=rows["chip_0402_spacing"]["status"],
                          report=str(report), sheets=sheets)
        except Exception as exc:
            result["error"] = str(exc)
        finally:
            if dialog:
                dialog._close()
            if root:
                root.destroy()
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        return 0 if result["valid"] else 1

    from src.bootstrap import prepare_startup
    try:
        context = prepare_startup(license_path=license_path)
    except (OSError, ValueError) as exc:
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()
        try:
            from src.licensing import machine_code
            messagebox.showerror("启动检查失败", str(exc) + "\n\n机器码：" + machine_code()
                                 + "\n请联系授权管理员或检查运行依赖。", parent=root)
        except ImportError:
            messagebox.showerror("启动检查失败", str(exc), parent=root)
        finally:
            root.destroy()
        return 1

    from src.ui.dfm_dialog import DFMDialog
    from src.ui.theme import enable_dpi_awareness

    enable_dpi_awareness()
    root = tk.Tk()
    root.withdraw()
    from src.diagnostics import install
    install(root)
    root._startup_context = context
    dialog = DFMDialog(root, brd_path=brd, standalone=True)
    if gerber:
        dialog._load_gerber_source(gerber)
    dialog.win.deiconify()
    dialog.win.mainloop()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # noqa: BLE001  窗口版没有控制台, 至少留个日志
        try:
            with open(_crash_path(), "w", encoding="utf-8") as f:
                f.write(traceback.format_exc())
        except OSError:
            pass
        raise
