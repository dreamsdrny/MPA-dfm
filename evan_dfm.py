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

    if "--check-smt-board-ui" in argv:
        import json
        from pathlib import Path
        from src.bootstrap import prepare_startup
        index=argv.index("--check-smt-board-ui")
        output=Path(argv[index+1]);output.parent.mkdir(parents=True,exist_ok=True)
        result={"valid":False}
        try:
            prepare_startup(license_path=license_path)
            from src.ui.pcb_diagnostic import check_packaged_smt_board
            result=check_packaged_smt_board(output,brd,argv[argv.index("--bom")+1] if "--bom" in argv else "")
        except Exception as exc:
            result["error"]=str(exc)
        output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
        return 0 if result["valid"] else 1

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
            from src.ui.pcb_diagnostic import check_packaged_pcb
            board_type = argv[argv.index("--board-type") + 1] if "--board-type" in argv else "rigid"
            result = check_packaged_pcb(output, board=brd, gerber=gerber,
                                         board_type=board_type, native="--native" in argv,
                                         bom=argv[argv.index("--bom")+1] if "--bom" in argv else "",smt="--smt" in argv)
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
                raise RuntimeError(f"主窗口未能打开走线查找页签：selected={dialog.workbench.select()!r} expected={str(trace)!r} "
                                   f"exists={trace.winfo_exists()} button={dialog.trace_filter_button.cget('state')}")
            from src.adapters.allegro_trace import TRACE_SKILL
            if set(trace.inputs) != {"layer", "width", "spacing"} or "MPA_TRACE_SEARCH" not in TRACE_SKILL:
                raise RuntimeError("走线查找输入或原生 SKILL 资源缺失")
            result = {"valid": True, "button": str(dialog.trace_filter_button.cget("text")),
                      "trace_title": "Allegro 走线查找 / 高亮", "native_brd_tested": False,
                      "integrated_tab": True, "direct_editor_launch": True,
                      "inputs": list(trace.inputs), "native_plugin_resources": True}
            dialog._close()
        except Exception as exc:  # noqa: BLE001  发布诊断保留具体失败而不是伪造成功
            result["error"] = str(exc)
        finally:
            if root:
                root.destroy()
        Path(argv[index + 1]).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        return 0 if result["valid"] else 1

    # 发布验收：真实创建总览查看器与进度动画，用合成底片走完整渲染链路。
    if "--check-viewer-ui" in argv:
        import json
        import time
        import tempfile
        from pathlib import Path
        from src.bootstrap import prepare_startup
        index = argv.index("--check-viewer-ui")
        if index + 1 >= len(argv):
            raise ValueError("--check-viewer-ui 需要指定结果 JSON 路径")
        output = Path(argv[index + 1])
        result = {"valid": False, "synthetic_data": True, "native_board_tested": False}
        root = None
        try:
            prepare_startup(license_path=license_path)
            from src.application.geometry_view import gerber_view
            from src.domain.geometry_view import viewport, zoom_viewport
            from src.ui.dfm_dialog import DFMDialog
            from src.ui.geometry_viewer import open_viewer
            folder = Path(tempfile.mkdtemp(prefix="evan-viewer-check-")) / "films"
            header = ("%FSLAX46Y46*%\n%MOMM*%\n%ADD10C,0.1*%\n"
                      "D10*\n")
            folder.mkdir(parents=True, exist_ok=True)
            for name, body in (("board.gtl", "X1000000Y2000000D02*\nX4000000Y2000000D01*\nM02*\n"),
                               ("board.gbl", "X1000000Y3000000D02*\nX4000000Y3000000D01*\nM02*\n"),
                               ("board.gts", "X1500000Y1000000D03*\nM02*\n"),
                               ("board.gbs", "X1500000Y1000000D03*\nM02*\n"),
                               ("board.gto", "X1000000Y1000000D02*\nX2000000Y1000000D01*\nM02*\n"),
                               ("board.gko", "X0Y0D02*\nX5000000Y0D01*\nX5000000Y5000000D01*\nX0Y5000000D01*\nX0Y0D01*\nM02*\n")):
                (folder / name).write_text(header + body, encoding="ascii")
            loaded = gerber_view(folder, issues=[dict(x_mm=2.5, y_mm=2.5, item="合成问题", status="fail")])
            model = loaded["model"]
            if len(model["layers"]) != 6 or not model["issues"]:
                raise RuntimeError("查看器图形层或问题标记数量不符合预期")
            area = viewport(model)
            zoomed = zoom_viewport(area, 4, anchor=(2.5, 2.5))
            if not (zoomed[0] < 2.5 < zoomed[2]):
                raise RuntimeError("缩放锚点计算不正确")
            root = tk.Tk()
            root.withdraw()
            dialog = DFMDialog(root, standalone=False)
            names = [dialog.workbench.tab(tab, "text") for tab in dialog.workbench.tabs()]
            if names[0] != "项目总览" or set(dialog.viewer_models) != {"gerber", "odb"}:
                raise RuntimeError("总览页或查看器入口缺失：" + str(names))
            if dialog.progress_matrix.state != "idle" or dialog.progress_matrix.snapshot()["filled"]:
                raise RuntimeError("未执行时进度动画不应有绿点")
            dialog._progress_start("overall")
            for stage in ("gerber", "drill", "smt_geometry"):
                dialog._progress_stage(stage)
            root.update()
            filled = dialog.progress_matrix.snapshot()
            if filled["state"] != "running" or filled["target"] <= 0:
                raise RuntimeError("阶段推进没有反映到进度动画：" + str(filled))
            dialog._progress_stage("未知阶段")
            root.update()
            if dialog.progress_matrix.snapshot()["target"] != filled["target"]:
                raise RuntimeError("未知阶段被当成进度推进，动画会谎报完成度")
            dialog._progress_finish("partial", label="仍有资料缺口")
            if dialog.progress_matrix.state != "partial":
                raise RuntimeError("资料缺口状态未体现")
            dialog._invalidate_results()
            if dialog.progress_matrix.state != "idle":
                raise RuntimeError("输入变化后动画没有复位")
            window = open_viewer(root, model, title="查看器自检", subtitle=loaded["source"])
            deadline = time.monotonic() + 25
            while time.monotonic() < deadline and "mm/px" not in window.viewer.scale_var.get():
                root.update()
                time.sleep(.01)
            if "mm/px" not in window.viewer.scale_var.get() or window.viewer._photo is None:
                raise RuntimeError("打包后查看器没有完成位图渲染：" + window.viewer.scale_var.get())
            before = window.viewer.visible_layer_names()
            window.viewer.toggle_layer(before[0])
            if window.viewer.visible_layer_names() != before[1:]:
                raise RuntimeError("图层显隐切换无效")
            rendered = window.viewer.scale_var.get()
            window.destroy()
            dialog._finish_close()
            result = {"valid": True, "layers": len(model["layers"]), "issues": len(model["issues"]),
                      "synthetic_data": True, "native_board_tested": False,
                      "overview_first_tab": names[0], "viewer_entries": sorted(dialog.viewer_models),
                      "animation_states": ["idle", "running", "partial"], "rendered": rendered}
        except Exception as exc:  # noqa: BLE001  保留真实失败，不伪装成通过
            result["error"] = str(exc)
        finally:
            if root:
                try:
                    root.destroy()
                except Exception:  # noqa: BLE001
                    pass
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
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
            from src.domain.smt_manufacturing import scan_assembly
            from src.application.version import BUILD_ID
            data["assembly_rules"]={"source":"合成验收工艺（非量产标准）","conveyor_clearance_top_mm":2}
            data["conveyed_edges"]=[dict(points=[[4,0],[4,20]],confirmed=True,assembly_sides=["TOP"],source="合成验收夹持边")]
            data["components"].append(dict(refdes="U1",side="BOTTOM",smd=True))
            data["pads"]=[dict(refdes="U1",pin=pin,side="BOTTOM",confirmed=True,outline=outline) for pin,outline in
                (("11",[[10,10],[11,10],[11,11],[10,11]]),("12",[[10.5,10],[11.5,10],[11.5,11],[10.5,11]]))]
            data["mask_openings"]=[dict(p,boundary="native_mask_opening") for p in data["pads"]]
            assembly=scan_assembly(data,{})
            analysis["assembly_findings"],analysis["assembly_coverage"],analysis["assembly_errors"]=assembly["findings"],assembly["coverage"],assembly["errors"]
            if not any(f["field"]=="conveyor_clearance" and f["status"]=="fail" for f in assembly["findings"]):
                raise RuntimeError("打包后R11传送边几何算法未产生预期缺陷")
            from src.domain.smt_pad_integrity import CHECKS as PAD_CHECKS
            if not set(PAD_CHECKS)<=set(c["check_key"] for c in assembly["coverage"]):raise RuntimeError("R12检查未打包")
            if not any(f["field"]=="smd_pin_spacing" and f["status"]=="fail" for f in assembly["findings"]) or not any(f["field"]=="smd_mask_bridge" for f in assembly["findings"]):
                raise RuntimeError("R12同器件重叠/开窗算法未产生预期问题")
            analysis["picture_geometry"]=dict(data,unit="mm",alignment_confirmed=True,source="合成打包验收对象")
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
            workbook = load_workbook(report)
            sheets = list(workbook.sheetnames)
            picture_count=len(workbook["Issue summary"]._images)
            if picture_count<1:
                raise RuntimeError("打包 EXE 未成功嵌入 Issue summary 问题图片")
            workbook.close()
            panel.supplier.set("同兴达 / TXD")
            panel._select_supplier()
            txd = panel.doc
            if len(txd.get("source_workbook",{}).get("sheets",[])) != 2 or len(txd.get("requirements",[])) != 108:
                raise RuntimeError("打包后的TXD双工作表内容不完整")
            panel.key.set("smt_cob");panel._render_fields()
            if len(panel.review_tree.get_children())!=54:raise RuntimeError("TXD工艺分面评审条目不完整")
            txd_result=analyze_smt_workflow(txd,"smt_cob",{})
            from src.application.reports import export_report
            selected=output.with_name("txd_selected.html")
            export_report("smt",selected,smt=txd_result)
            if selected.with_suffix(".xlsx").exists() or selected.with_suffix(".txt").exists():
                raise RuntimeError("所选报告格式产生了未选择的附属文件")
            panel.supplier.set("立景 / Lijing");panel.load_builtin();panel._render_result(analysis,panel._revision)
            result.update(valid=True, build_id=BUILD_ID, r11_conveyor_checked=True, r12_pin_mask_checked=True, factory=doc["meta"]["factory"],
                          r14_txd_checked=True,txd_sheets=2,txd_rows=108,selected_report=str(selected),
                          overall_button=dialog.run_button.cget("text"),analysis_export_button=dialog.analysis_export_button.cget("text"),
                          numeric_rules=len(doc["profiles"]["automotive"]["rules"]),
                          manual_conditions=len(panel.review_tree.get_children()),
                          rendered_items=len(panel.tree.get_children()),
                          baseline_rows=len(panel.baseline_tree.get_children()),
                          mixed_spacing_status=rows["chip_0402_spacing"]["status"],
                          report=str(report), sheets=sheets,issue_pictures=picture_count,
                          assembly_coverage_rows=len(panel.assembly_tree.get_children()),bom_import_button=panel.bom_button.cget("text"))
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
