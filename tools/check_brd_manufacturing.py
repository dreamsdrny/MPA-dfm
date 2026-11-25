"""One BRD -> native Gerber, Drill, Route, ODB++ -> combined DFM + audit report."""
import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.brd_design import extract_brd_design, to_design_params
from src.brd_input import brd_signature
from src.services.pcb_analysis import PCBAnalysisRequest, run_pcb_analysis
from src.workbench_report import export_report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--brd", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--editor", default="")
    parser.add_argument("--odb-exporter", default="")
    parser.add_argument("--factory", default="hongban")
    parser.add_argument("--copper-um", type=float, default=35)
    parser.add_argument("--timeout", type=int, default=180)
    args = parser.parse_args()
    board = Path(args.brd).resolve()
    signature = brd_signature(str(board))
    info = extract_brd_design(str(board))
    if not info.get("ok"):
        raise ValueError(info.get("error") or "BRD 无法解析")
    design = to_design_params(info, copper_um=args.copper_um)
    design["_brd_signature"] = signature
    request = PCBAnalysisRequest(str(board), design, factory=args.factory, editor_path=args.editor,
                                 odb_exporter=args.odb_exporter, output_dir=args.output, timeout=args.timeout,
                                 design_unit=info.get("units", ""))
    result = run_pcb_analysis(request, progress=lambda stage: print(stage, flush=True))
    folder = Path(result["manufacturing_export"]["directory"])
    export_report("pcb", folder/(board.stem+"_PCB制造检查.xlsx"), pcb=result,
                  board_meta=dict(board_name=board.stem, brd_path=str(board)))
    print(result["verdict"], folder)


if __name__ == "__main__":
    main()
