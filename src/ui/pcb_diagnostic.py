"""Authorized EXE acceptance through the actual unified GUI and worker routes.

Called only after prepare_startup succeeds. Inputs remain read-only; diagnostic
reports and native exports stay below the explicitly requested output directory.
"""
import time
from pathlib import Path
from unittest.mock import patch

from src.application.version import BUILD_ID
from src.infrastructure.snapshots import capture_sources, verify_sources
from src.infrastructure.diagnostic_output import save_analysis_evidence


def check_packaged_smt_board(output, board, bom=""):
    """SMT-only actual GUI acceptance, including asynchronous BOM browse path."""
    import tkinter as tk
    from src.ui.dfm_dialog import DFMDialog
    from src.application.reports import export_report
    output=Path(output).resolve()
    if not board:raise ValueError("SMT验收必须选择BRD")
    sources=capture_sources([board,bom] if bom else [board])
    errors=[];launches=[];root=tk.Tk();root.withdraw();dialog=None
    root.report_callback_exception=lambda *args:errors.append(str(args[1]))
    try:
        with patch("src.ui.prefs.save_prefs"),patch("src.ui.prefs.load_prefs",return_value={}), \
             patch("src.ui.paths.default_output_dir",return_value=str(output.parent/"app-output")), \
             patch("src.ui.dfm_dialog.messagebox.showerror",side_effect=lambda *args,**kw:errors.append(str(args))), \
             patch("src.ui.smt_panel.messagebox.showerror",side_effect=lambda *args,**kw:errors.append(str(args))), \
             patch("src.ui.dfm_dialog.AllegroSession",side_effect=lambda *args,**kw:launches.append(args)):
            dialog=DFMDialog(root,standalone=True);dialog.sync_editor.set(False)
            def wait():
                deadline=time.monotonic()+900
                while (dialog.tasks.pending or dialog._loading) and time.monotonic()<deadline:
                    root.update();time.sleep(.01)
                root.update()
                if errors:raise ValueError("SMT界面异常："+"；".join(errors))
                if dialog.tasks.pending:raise TimeoutError("SMT界面验收超时")
            dialog.brd_path.set(board);dialog._load_from_brd();wait()
            if not dialog.brd_info:raise ValueError(dialog._brd_error)
            if bom:
                with patch("src.ui.smt_panel.filedialog.askopenfilename",return_value=bom):dialog.smt._browse_bom()
                wait()
                if dialog.smt.bom_path.get()!=bom:raise ValueError("BOM导入没有被界面采用")
            dialog.workbench.select(dialog.smt);dialog.smt.run();wait()
            result=dialog.smt.last_result
            if not result:raise ValueError("SMT未产生分析结果")
            report=output.with_suffix(".xlsx");export_report("smt",report,smt=result,board_meta={"brd_path":board})
            from openpyxl import load_workbook
            wb=load_workbook(report);images=len(wb["Issue summary"]._images);sheets=wb.sheetnames;wb.close()
            verify_sources(sources)
            if launches:raise ValueError("SMT分析不应启动PCB Editor")
            target=[f for f in result.get("assembly_findings",[]) if f.get("refdes")=="U1" and f.get("other_refdes")=="U1" and {f.get("pin"),f.get("other_pin")}=={"11","12"}]
            save_analysis_evidence(output.with_name(output.stem+"_analysis.json"),result)
            return dict(valid=True,build_id=BUILD_ID,verdict=result["verdict"],bom_imported=bool(bom),bom_total=result.get("bom_analysis",{}).get("total"),
                        alternate_records=result.get("bom_analysis",{}).get("alternate_records"),coverage_count=len(result["assembly_coverage"]),
                        displayed_coverage=len(dialog.smt.assembly_tree.get_children()),target_pin_issues=target,source_unchanged=True,
                        source_fingerprints=sources,editor_launches=launches,callback_errors=errors,report=str(report),images=images,sheets=sheets,
                        assembly_errors=result.get("assembly_errors",[]))
    finally:
        if dialog:dialog._finish_close()
        elif root.winfo_exists():root.destroy()


def check_packaged_pcb(output, board="", gerber="", board_type="rigid", native=False, bom="", smt=False):
    import tkinter as tk
    from src.ui.dfm_dialog import DFMDialog
    from src.application.reports import export_report

    output = Path(output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    inputs = [path for path in (board, gerber, bom) if path]
    if not inputs:
        raise ValueError("PCB 验收需提供 --brd 或 --gerber")
    for path in inputs:
        source = Path(path).resolve()
        if output == source or source.is_dir() and output.is_relative_to(source):
            raise ValueError("验收输出不得覆盖源资料")
    sources = capture_sources(inputs)
    errors = []
    editor_launches = []
    root = tk.Tk()
    root.withdraw()
    dialog = None
    root.report_callback_exception = lambda *args: errors.append(str(args[1]))
    try:
        with patch("src.ui.prefs.save_prefs"), patch("src.ui.prefs.load_prefs", return_value={}), \
                patch("src.ui.dfm_dialog.messagebox.showerror", side_effect=lambda *args, **kw: errors.append(str(args))), \
                patch("src.ui.dfm_dialog.messagebox.showwarning", side_effect=lambda *args, **kw: errors.append(str(args))), \
                patch("src.ui.gerber_panel.messagebox.showerror", side_effect=lambda *args, **kw: errors.append(str(args))), \
                 patch("src.ui.smt_panel.messagebox.showerror",side_effect=lambda *args,**kw:errors.append(str(args))), \
                 patch("src.ui.paths.default_output_dir",return_value=str(output.parent/"app-output")), \
                 patch("src.ui.dfm_dialog.AllegroSession", side_effect=lambda *args, **kw: editor_launches.append(args)):
            dialog = DFMDialog(root, standalone=True)
            # This command validates manufacturing workers, not interactive CAD.
            dialog.sync_editor.set(False)
            hidden_before_ready = root.state() == "withdrawn"
            def wait(predicate):
                deadline = time.monotonic() + 900
                while not predicate() and time.monotonic() < deadline:
                    root.update()
                    time.sleep(.01)
                root.update()
                if errors:
                    raise ValueError("界面分析异常：" + "；".join(errors))
                if not predicate():
                    raise TimeoutError("PCB 界面验收超过 900 秒")
            dialog.params.board_type.set(board_type)
            dialog.params.sync_levels()
            if board:
                dialog.brd_path.set(str(Path(board).resolve()))
                dialog._load_from_brd()
                wait(lambda: not dialog._loading and not dialog.tasks.pending)
                if not dialog.brd_info:
                    raise ValueError(dialog._brd_error)
            if gerber and not native:
                dialog._load_gerber_source(str(Path(gerber).resolve()))
                wait(lambda: not dialog.tasks.pending)
            dialog.auto_manufacturing.set(native)
            dialog._pcb_output_dir = lambda: str(output.parent / "native_exports")
            # Exercise tab-independent dispatch: the analysis must still use
            # the loaded source, not accidentally a different notebook page.
            dialog.workbench.select(dialog.brd_body)
            root.update_idletasks()
            before_bounds = [dialog.workbench.winfo_width(), dialog.workbench.winfo_height(),
                             dialog.results.winfo_width(), dialog.results.winfo_height()]
            dialog._run()
            wait(lambda: not dialog.tasks.pending)
            result = dialog.last_result
            if not result or dialog._pcb_state != "complete":
                raise ValueError("PCB 未产生已完成的结果：" + dialog.status_var.get())
            root.update_idletasks()
            after_bounds = [dialog.workbench.winfo_width(), dialog.workbench.winfo_height(),
                            dialog.results.winfo_width(), dialog.results.winfo_height()]
            if before_bounds != after_bounds:
                raise ValueError(f"分析前后布局尺寸变化：{before_bounds} -> {after_bounds}")
            verify_sources(sources)
            if editor_launches:
                raise ValueError("PCB 分析不应启动图形 Editor")
            report = output.with_suffix(".xlsx")
            export_report("pcb", report, pcb=result, board_meta={"board_name": result.get("board_name", "")})
            smt_evidence=None
            if smt:
                dialog.smt.bom_path.set(bom)
                dialog.workbench.select(dialog.smt)
                dialog.smt.run()
                wait(lambda:not dialog.tasks.pending)
                smt_result=dialog.smt.last_result
                if not smt_result:raise ValueError("真实SMT GUI未返回结果")
                smt_path=output.with_name(output.stem+"_SMT.xlsx")
                combined_path=output.with_name(output.stem+"_DFM.xlsx")
                export_report("smt",smt_path,smt=smt_result,board_meta={"brd_path":board})
                export_report("dfm",combined_path,pcb=result,smt=smt_result,board_meta={"brd_path":board})
                from openpyxl import load_workbook
                workbook=load_workbook(combined_path)
                pictures=len(workbook["Issue summary"]._images)
                workbook.close()
                smt_evidence=dict(verdict=smt_result["verdict"],bom_count=smt_result.get("bom_analysis",{}).get("total"),
                    assembly_count=len(smt_result.get("assembly_findings",[])),coverage_count=len(smt_result.get("assembly_coverage",[])),
                    displayed_coverage=len(dialog.smt.assembly_tree.get_children()),combined_pictures=pictures,smt_report=str(smt_path),combined_report=str(combined_path))
            payload = dict(valid=True, build_id=BUILD_ID, input_brd=board, input_gerber=gerber,
                board_type=board_type, analysis_mode=result["analysis_mode"],
                geometry_executed=result.get("geometry_executed"), verdict=result.get("verdict"),
                pcb_state=dialog._pcb_state, shared_result_panel=dialog.results is dialog.gerber_panel.results,
                tabs=[dialog.workbench.tab(tab, "text") for tab in dialog.workbench.tabs()],
                rendered_rows=len(dialog.results.tree.get_children()), callback_errors=errors,
                total_result_items=len(dialog.results._items), displayed_location_rows=len(dialog.results.location_tree.get_children()),
                page_size=dialog.results.page_size, fixed_workbench=True,
                hidden_before_ready=hidden_before_ready, before_bounds=before_bounds, after_bounds=after_bounds,
                layout_stable=before_bounds == after_bounds,
                source_unchanged=True, source_fingerprints=sources, report=str(report),
                editor_launches=editor_launches, headless_extraction=(result.get("native_extraction") or {}).get("status"),
                component_coordinate_count=len(result.get("component_coordinates", [])),
                copper_um=result.get("copper_um"), stackup=result.get("stackup", []),
                preview=result.get("gerber_preview", ""),
                smt=smt_evidence,
                stages={kind:entry["status"] for kind,entry in result.get("manufacturing_export", {}).get("stages", {}).items()})
            verify_sources(sources)
            save_analysis_evidence(output.with_name(output.stem + "_analysis.json"), result)
            return payload
    finally:
        if dialog:
            dialog._finish_close()
        elif root.winfo_exists():
            root.destroy()
