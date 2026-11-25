"""Read existing generated example films in an isolated copy, not the mixed output tree."""
import json
from pathlib import Path
import shutil
import sys
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.application.gerber_analysis import GerberAnalysisRequest, import_gerber, run_gerber_analysis
from src.application.reports import export_report


def main():
    root = Path(__file__).resolve().parents[1]
    destination = root / "delivery" / "evidence" / "GERBER_20261125_Final"
    destination.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="evan-gerber-example-") as temp:
        folder = Path(temp)
        originals = sorted((root / "output").glob("DEMO_BOARD*.gbr"))
        originals += sorted((root / "output").glob("DEMO_BOARD*.drl"))
        for source in originals:
            shutil.copy2(source, folder / source.name)
        info = import_gerber(str(folder))
        result = run_gerber_analysis(GerberAnalysisRequest(str(folder), tuple(info["input_fingerprints"]),
                                    {"layer_count": 4, "copper_um": 35}))
        export_report("pcb", str(destination / "existing_example.xlsx"), result)
        summary = {"input_kind": "project-generated preview example; not production data",
                   "original_files_modified": False, "brd_required": False,
                   "layers": info["layers"], "errors": info["errors"],
                   "geometry_executed": result["geometry_executed"], "verdict": result["verdict"],
                   "locations": len(result.get("problem_locations", [])), "releasable": result["releasable"],
                   "preview": result["gerber_preview"]}
        (destination / "existing_example.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"layers": len(info["layers"]), "locations": summary["locations"],
                          "geometry": result["geometry_executed"], "verdict": result["verdict"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
