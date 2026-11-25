"""Authorized EXE acceptance through the actual unified GUI and worker routes.

Called only after prepare_startup succeeds. Inputs remain read-only; diagnostic
reports and native exports stay below the explicitly requested output directory.
"""
import json
import time
from pathlib import Path
from unittest.mock import patch

from src.application.version import BUILD_ID
from src.infrastructure.snapshots import capture_sources, verify_sources


def check_packaged_pcb(output, board="", gerber="", board_type="rigid", native=False):
    import tkinter as tk
    from src.ui.dfm_dialog import DFMDialog
    from src.application.reports import export_report

    output = Path(output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    inputs = [path for path in (board, gerber) if path]
    if not inputs:
        raise ValueError("PCB 验收需提供 --brd 或 --gerber")
    for path in inputs:
        source = Path(path).resolve()
        if output == source or source.is_dir() and output.is_relative_to(source):
            raise ValueError("验收输出不得覆盖源资料")
    sources = capture_sources(inputs)
    errors = []
    root = tk.Tk()
    root.withdraw()
    dialog = None
    root.report_callback_exception = lambda *args: errors.append(str(args[1]))
    try:
        with patch("src.ui.prefs.save_prefs"), patch("src.ui.prefs.load_prefs", return_value={}), \
                patch("src.ui.dfm_dialog.messagebox.showerror", side_effect=lambda *args, **kw: errors.append(str(args))), \
                patch("src.ui.dfm_dialog.messagebox.showwarning", side_effect=lambda *args, **kw: errors.append(str(args))), \
                patch("src.ui.gerber_panel.messagebox.showerror", side_effect=lambda *args, **kw: errors.append(str(args))):
            dialog = DFMDialog(root, standalone=True)
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
            dialog._run()
            wait(lambda: not dialog.tasks.pending)
            result = dialog.last_result
            if not result or dialog._pcb_state != "complete":
                raise ValueError("PCB 未产生已完成的结果：" + dialog.status_var.get())
            verify_sources(sources)
            report = output.with_suffix(".xlsx")
            export_report("pcb", report, pcb=result, board_meta={"board_name": result.get("board_name", "")})
            payload = dict(valid=True, build_id=BUILD_ID, input_brd=board, input_gerber=gerber,
                board_type=board_type, analysis_mode=result["analysis_mode"],
                geometry_executed=result.get("geometry_executed"), verdict=result.get("verdict"),
                pcb_state=dialog._pcb_state, shared_result_panel=dialog.results is dialog.gerber_panel.results,
                tabs=[dialog.workbench.tab(tab, "text") for tab in dialog.workbench.tabs()],
                rendered_rows=len(dialog.results.tree.get_children()), callback_errors=errors,
                source_unchanged=True, source_fingerprints=sources, report=str(report),
                preview=result.get("gerber_preview", ""),
                stages={kind:entry["status"] for kind,entry in result.get("manufacturing_export", {}).get("stages", {}).items()})
            verify_sources(sources)
            output.with_name(output.stem + "_analysis.json").write_text(
                json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
            return payload
    finally:
        if dialog:
            dialog._finish_close()
        elif root.winfo_exists():
            root.destroy()
