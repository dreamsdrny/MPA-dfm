"""Packaged acceptance with synthetic CAM files, never user board data."""
from pathlib import Path
from tempfile import TemporaryDirectory

from src.application.gerber_analysis import (
    GerberAnalysisRequest,
    import_gerber,
    run_gerber_analysis,
)
from src.application.reports import export_report


def check_packaged_gerber(output):
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="evan-gerber-acceptance-") as temp:
        folder = Path(temp)
        header = "%FSLAX46Y46*%\n%MOMM*%\n%ADD10C,0.1*%\nD10*\n"
        for ext in ("gtl", "gbl", "gto"):
            (folder / f"sample.{ext}").write_text(header + "X1000000Y1000000D02*\nX2000000Y1000000D01*\nM02*\n")
        for ext in ("gts", "gbs"):
            (folder / f"sample.{ext}").write_text(header.replace("0.1", "0.5") + "X1500000Y1000000D03*\nM02*\n")
        (folder / "sample.gko").write_text(header + "X0Y0D02*\nX5000000Y0D01*\nX5000000Y5000000D01*\nX0Y5000000D01*\nX0Y0D01*\nM02*\n")
        info = import_gerber(str(folder))
        result = run_gerber_analysis(GerberAnalysisRequest(str(folder), tuple(info["input_fingerprints"]),
                                    {"copper_um": 35, "layer_count": 2}))
        report = output.with_name("gerber_packaged.xlsx")
        export_report("pcb", str(report), result, board_meta={"board_name": "合成 Gerber 发布验收"})
        from openpyxl import load_workbook
        workbook = load_workbook(report, read_only=True)
        sheets = workbook.sheetnames
        workbook.close()
        return {"synthetic_data": True, "brd_required": False, "cadence_required": False,
                "layers": len(result["gerber_layers"]), "problem_locations": len(result["problem_locations"]),
                "geometry_executed": result["geometry_executed"], "releasable": result["releasable"],
                "report": str(report), "sheets": sheets, "preview": result["gerber_preview"]}
