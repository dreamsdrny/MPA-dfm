"""Single-board object DFM without BRD/Artwork; never edits the input archive.

python tools/check_odb_dfm.py --input board.tgz --output output/board_object_dfm
"""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.odb_dfm import analyze_odb
from src.design_inputs.coordination_report import export_coordination


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--step")
    parser.add_argument("--rules", help="JSON object_rules overrides; limits must be confirmed")
    args = parser.parse_args()
    source, output = Path(args.input).resolve(), Path(args.output).resolve()
    if output == source or source.is_dir() and output.is_relative_to(source):
        raise ValueError("输出不可覆盖原始制造资料")
    output.mkdir(parents=True, exist_ok=True)
    overrides = json.loads(Path(args.rules).read_text(encoding="utf-8-sig")) if args.rules else None
    result = analyze_odb(source, step=args.step, object_rules=overrides,
                         preview_path=output/"object_dfm_preview.html")
    (output/"object_dfm.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    # Use shared report writer, without manufacturing fictitious BRD design values.
    report = dict(kind="odb", status="review", layout=dict(selected_step=result["selected_step"], films=[]),
                  sources=[result["input"]], warnings=result["warnings"] + result["errors"],
                  components=[], object_dfm=result)
    export_coordination(report, output/"object_dfm.xlsx")
    print(json.dumps(dict(verdict=result["verdict"], scope=result["scope"],
                          objects=result["object_counts"], findings=result["rule_counts"],
                          output=str(output)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
