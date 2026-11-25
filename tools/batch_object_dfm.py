"""Read-only corpus acceptance. Isolated workers, timeouts, hashes and explicit gaps.

python tools/batch_object_dfm.py --root D:/Evan/EQ-DFM/26 --output output/object_dfm_20261123
"""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from time import perf_counter
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def sha256(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024*1024), b""):
            h.update(block)
    return h.hexdigest()


def worker(kind, path):
    if kind == "odb":
        from src.odb_dfm import analyze_odb
        return analyze_odb(path, scan_limits=dict(max_candidates=200_000, max_findings=10_000))
    if kind == "brd":
        from src.brd_design import extract_brd_design
        result = extract_brd_design(path)
        return dict(kind=kind, parsed=result.get("ok"), layer_count=result.get("layer_count"),
                    warnings=result.get("warnings", []), geometry_complete=False, releasable=False,
                    scope="BRD 参数解析；完整逐盘/网络几何需 ODB++ 或同版导出，非对象 DFM 完成")
    from src.gerber_analysis import analyze_gerbers
    result = analyze_gerbers(path, geometry_options=dict(scan_limits=dict(max_candidates=200_000, max_findings=2000)))
    result.pop("films", None)  # GEOS values are not JSON report data.
    result["scope"] = "现有 Gerber 几何初筛；缺叠构/对象工艺不猜测；不是完整 DFM 放行"
    result["releasable"] = False
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root")
    parser.add_argument("--output")
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--jobs", type=int, default=3)
    parser.add_argument("--kinds", nargs="+", choices=("odb", "brd", "gerber"), default=("odb", "brd", "gerber"))
    parser.add_argument("--retry-errors", action="store_true", help="Retry failed jobs in this output; keep other completed reports")
    parser.add_argument("--worker", choices=("odb", "brd", "gerber"))
    parser.add_argument("--input")
    args = parser.parse_args()
    if args.worker:
        print(json.dumps(worker(args.worker, args.input), ensure_ascii=False, allow_nan=False))
        return
    root, output = Path(args.root).resolve(), Path(args.output).resolve()
    if output.is_relative_to(root):
        raise ValueError("验收输出不得写入原始资料目录")
    output.mkdir(parents=True, exist_ok=True)
    paths = sorted(p for p in root.rglob("*") if p.is_file())
    inventory, candidates, seen = [], [], {}
    gerber_dirs = set()
    from src.gerber_export import GERBER_EXTENSIONS
    for p in paths:
        suffix = p.suffix.lower()
        row = dict(path=str(p), size=p.stat().st_size, modified_ns=p.stat().st_mtime_ns, extension=suffix,
                   status="supporting_document_not_executed")
        kind = None
        if suffix == ".brd":
            kind = "brd"
        elif suffix in (".tgz", ".gz"):
            kind = "odb"
        elif suffix == ".zip":
            try:
                with zipfile.ZipFile(p) as z:
                    names = [n.lower() for n in z.namelist()]
                    if any(n.endswith(("matrix/matrix", "matrix/matrix.gz", "matrix/matrix.z")) for n in names):
                        kind = "odb"
                    else:
                        row["status"] = "archive_inventory_only"
            except (OSError, zipfile.BadZipFile) as exc:
                row.update(status="archive_error", error=str(exc))
        elif suffix in (".rar", ".7z"):
            row["status"] = "unsupported_archive"
        elif suffix in GERBER_EXTENSIONS:
            gerber_dirs.add(p.parent)
            row["status"] = "gerber_directory_scan"
        if kind:
            row["sha256"] = sha256(p)
            key = (kind, row["sha256"])
            if key in seen:
                row.update(status="duplicate", same_as=seen[key])
            else:
                seen[key] = str(p)
                row["status"] = "scheduled"
                candidates.append((kind, p))
        inventory.append(row)
    # Some ODB jobs are already extracted as directories.
    for matrix in root.rglob("matrix"):
        if matrix.is_dir() and any((matrix/n).is_file() for n in ("matrix", "matrix.gz", "matrix.Z")):
            candidates.append(("odb", matrix.parent))
    candidates.extend(("gerber", p) for p in sorted(gerber_dirs))
    candidates = [(kind, path) for kind, path in candidates if kind in args.kinds]
    previous = []
    if args.retry_errors and (output/"summary.json").is_file():
        previous = json.loads((output/"summary.json").read_text(encoding="utf-8"))["records"]
        completed = {(r["kind"], r["path"]) for r in previous if r["status"] not in ("error", "timeout_incomplete")}
        candidates = [(kind, path) for kind, path in candidates if (kind, str(path)) not in completed]
        previous = [r for r in previous if (r["kind"], r["path"]) in completed]
    (output/"inventory.json").write_text(json.dumps(inventory, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Inventory {len(paths)} files; jobs {Counter(kind for kind, _ in candidates)}", flush=True)
    records = list(previous)
    total_jobs = len(previous) + len(candidates)

    def run(job):
        kind, path = job
        key = hashlib.sha256((kind+str(path)).encode()).hexdigest()[:16]
        start = perf_counter()
        digest = sha256(path) if path.is_file() else None
        record = dict(kind=kind, path=str(path), before_sha256=digest, report=str(output/(key+".json")))
        try:
            process = subprocess.run([sys.executable, "-X", "utf8", __file__, "--worker", kind,
                                      "--input", str(path)], capture_output=True, timeout=args.timeout)
            if process.returncode:
                record.update(status="error", error=process.stderr.decode("utf-8", "replace")[-4000:])
            else:
                result = json.loads(process.stdout.decode("utf-8"))
                Path(record["report"]).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
                record.update(status=("parsed_parameters_only" if result.get("parsed") else "parameters_unavailable")
                              if kind == "brd" else "executed_partial",
                              objects=result.get("object_counts", {}), findings=len(result.get("checks", [])),
                              rule_counts=dict(Counter(c["field"] for c in result.get("checks", []))),
                              input_errors=len(result.get("errors", [])), releasable=False)
        except subprocess.TimeoutExpired:
            record.update(status="timeout_incomplete", error=f"超过 {args.timeout}s；不能作通过")
        except (ValueError, OSError) as exc:
            record.update(status="error", error=str(exc))
        record["seconds"] = round(perf_counter()-start, 3)
        record["after_sha256"] = sha256(path) if path.is_file() else None
        record["source_unchanged"] = digest == record["after_sha256"]
        return record

    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        futures = [pool.submit(run, job) for job in candidates]
        for future in as_completed(futures):
            record = future.result()
            records.append(record)
            with (output/"progress.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(record, ensure_ascii=False)+"\n")
            print(f"{len(records)}/{total_jobs} {record['kind']} {record['status']} {Path(record['path']).name}", flush=True)
    changed = [row["path"] for row in inventory if not Path(row["path"]).is_file() or
               Path(row["path"]).stat().st_size != row["size"] or Path(row["path"]).stat().st_mtime_ns != row["modified_ns"]]
    summary = dict(root=str(root), files=len(paths), inventory_statuses=dict(Counter(r["status"] for r in inventory)),
                   job_count=len(records), job_statuses=dict(Counter(r["status"] for r in records)),
                   source_stat_changes=changed, all_hashed_inputs_unchanged=all(r["source_unchanged"] for r in records),
                   scope="全资料盘点；可识别 ODB++/Gerber 执行部分几何；BRD 参数解析；支持文档/未解包归档非已检查",
                   records=records)
    (output/"summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "批量执行摘要"
    ws.append(["格式", "资料", "执行状态", "问题数", "输入错误数", "耗时秒", "原始哈希一致", "报告", "错误"])
    for r in records:
        ws.append([r.get(k) for k in ("kind", "path", "status", "findings", "input_errors", "seconds", "source_unchanged", "report", "error")])
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    findings = wb.create_sheet("实际对象问题明细")
    columns = ("item", "status", "layer", "object_id", "other_object_id", "refdes", "pin", "other_refdes", "other_pin",
               "net", "other_net", "measured", "capability", "unit", "x_mm", "y_mm", "note", "rule_sha256")
    findings.append(["制造资料", "检查项", "状态", "层", "对象", "另一对象", "位号", "引脚", "另一位号", "另一引脚",
                     "网络", "另一网络", "实测", "规则", "单位", "X mm", "Y mm", "说明", "有效规则SHA256"])
    coverage = wb.create_sheet("对象覆盖与缺口")
    coverage.append(["制造资料", "规则", "状态", "已测", "缺失", "说明", "所需资料"])
    for row in records:
        if row["kind"] != "odb" or row["status"] != "executed_partial":
            continue
        result = json.loads(Path(row["report"]).read_text(encoding="utf-8"))
        for finding in result["checks"]:
            findings.append([row["path"], *[finding.get(key) for key in columns]])
        for item in result["geometry_coverage"]:
            coverage.append([row["path"], *[item.get(key) for key in ("rule", "status", "checked", "missing", "note", "required_data")]])
    for sheet in wb:
        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = sheet.dimensions
        for column in sheet.columns:
            sheet.column_dimensions[column[0].column_letter].width = 28
        for row in sheet:
            for cell in row:
                if isinstance(cell.value, str):
                    cell.data_type = "s"  # Untrusted refdes/paths are never formulas.
    wb.save(output/"batch_summary.xlsx")
    print(json.dumps({k: v for k, v in summary.items() if k != "records"}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
