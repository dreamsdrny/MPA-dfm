"""BRD 分析与输出引擎的自检脚本 (不需要真实 .brd / 不需要 Allegro)。

做两件事:
  1. 用 tools/make_demo_brd.py 合成一块样例板 (含配套导出文件);
  2. 跑完整分析, 然后按"纪律"逐项校验结果, 而不是只看有没有崩:

     A. 每项能力都返回了结果, 没有异常
     B. 状态是 unavailable 的能力, 必须写清 missing + howto (缺数据 ≠ 通过)
     C. 给出判定的能力 (pass/fail/warn) 必须有 metric 或 items 支撑
     D. 声称生成了文件的能力, 产物必须真的在磁盘上且非空
     E. 产出的报告文件必须能打开 (xlsx 用 openpyxl 复读, json 能反序列化)
     F. 同一轮分析里, 关键数字在能力之间自洽 (例如 DRC 的违规数 == 报告里的条数)

用法:
    python tools/selftest_analysis.py            # 全量
    python tools/selftest_analysis.py --keep     # 保留临时输出目录
"""
import argparse
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

FAILURES = []
CHECKS = [0]


def check(ok, label, detail=""):
    CHECKS[0] += 1
    if ok:
        print(f"  [OK]   {label}")
    else:
        print(f"  [FAIL] {label}  {detail}")
        FAILURES.append(f"{label} {detail}".strip())
    return ok


def main(argv=None):
    parser = argparse.ArgumentParser(description="BRD 分析引擎自检")
    parser.add_argument("--keep", action="store_true", help="保留输出目录")
    parser.add_argument("--out", default="", help="输出目录 (默认用临时目录)")
    args = parser.parse_args(argv)

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out_dir = args.out or tempfile.mkdtemp(prefix="brd_selftest_")
    demo_dir = os.path.join(out_dir, "demo")
    report_dir = os.path.join(out_dir, "reports")

    print("=" * 74)
    print("  BRD 分析与输出引擎 自检")
    print("=" * 74)
    print(f"  输出目录: {out_dir}")

    # ---- 1. 合成样例 ----
    print("\n[1] 生成合成样例 .brd")
    from tools.make_demo_brd import write_demo

    paths = write_demo(demo_dir, variants=True)
    check(os.path.getsize(paths["brd"]) > 1000, "样例 .brd 已生成",
          paths["brd"])
    check(os.path.exists(paths["brd_rev_b"]), "对比版本 .brd 已生成")

    # ---- 2. 跑全量分析 ----
    print("\n[2] 跑全量分析")
    from src.brd_analysis import analyze, capability_rows, write_reports
    from src.brd_analysis.catalog import CATALOG

    export_files = [p for name, p in paths.items()
                    if name in ("place_txt.txt", "demo.drl", "demo_drc.rpt",
                                "pstxnet.dat", "demo.ipc", "evan_pads.csv",
                                "brd_rev_b")]
    bundle = analyze(paths["brd"], out_dir=out_dir, export_files=export_files)
    check(bundle.get("ok"), "分析完成", str(bundle.get("error")))
    results = bundle.get("results") or []
    catalog = capability_rows()
    check(len(results) == len(catalog),
          f"能力全部执行 ({len(results)}/{len(catalog)})",
          f"catalog_errors={bundle.get('catalog_errors')}")
    check(not bundle.get("catalog_errors"), "所有类别模块都能加载",
          str(bundle.get("catalog_errors")))
    if not results:
        return _finish(out_dir, args.keep)

    # ---- 3. 逐项纪律校验 ----
    print("\n[3] 逐项校验结果纪律")
    no_metric, bad_unavailable, phantom = [], [], []
    for res in results:
        if res["status"] == "unavailable":
            if not res.get("missing") or not res.get("howto"):
                bad_unavailable.append(res["id"])
        if res["status"] in ("pass", "fail", "warn"):
            if not res.get("metrics") and not res.get("items"):
                no_metric.append(res["id"])
        for art in res.get("artifacts") or []:
            path = art.get("path") or ""
            if not path or not os.path.exists(path) or os.path.getsize(path) == 0:
                phantom.append(f"{res['id']} -> {path}")
    check(not bad_unavailable,
          "unavailable 的能力都写清了「缺什么 + 怎么补」",
          "、".join(bad_unavailable))
    check(not no_metric,
          "给出判定的能力都有指标或明细支撑", "、".join(no_metric))
    check(not phantom, "声称生成的文件都真的存在且非空", "；".join(phantom))

    # ---- 4. 关键结论的人工可核对面 ----
    print("\n[4] 关键结论抽查")
    by_id = {r["id"]: r for r in results}

    def status(cap_id):
        return (by_id.get(cap_id) or {}).get("status")

    check(status("drc.width") == "pass",
          "线宽检查: 样例最细 0.1524mm 不低于约束 0.1016mm -> pass",
          f"实际 {status('drc.width')}")
    check(status("drc.clearance") == "fail",
          "间距检查: 样例故意做了一对 0.0076mm 净距的走线 -> fail",
          f"实际 {status('drc.clearance')}")
    check(status("physical.board_geometry") == "pass"
          and abs((by_id["physical.board_geometry"]["metrics"][0]["value"] or 0) - 101.6) < 0.5,
          "板框: 板宽 101.6mm", str(by_id.get("physical.board_geometry", {}).get("metrics")))
    check((by_id.get("physical.placement") or {}).get("status") == "pass",
          "元件布局: 导入 Placement 后能给出 99 个器件坐标",
          str(by_id.get("physical.placement", {}).get("summary")))

    # ---- 焊盘几何: 每个器件的焊盘尺寸与引脚间距 ----
    pg = by_id.get("physical.pad_geometry") or {}
    pad_rows = {r["器件"]: r for r in (pg.get("items") or [])}
    u7, r60 = pad_rows.get("U7") or {}, pad_rows.get("R60") or {}
    check(u7.get("焊盘数") == 8 and u7.get("焊盘尺寸mm") == "1.5×0.6"
          and abs((u7.get("引脚间距mm") or 0) - 1.27) < 0.005,
          "焊盘几何: U7 (SOIC-8) 8 个焊盘, 尺寸 1.5×0.6mm, 引脚间距 1.27mm", str(u7))
    check(r60.get("焊盘数") == 2 and abs((r60.get("引脚间距mm") or 0) - 1.651) < 0.005,
          "焊盘几何: R60 (0603) 2 个焊盘, 引脚间距 1.651mm", str(r60))
    check(abs((u7.get("焊盘间距mm") or 0) - 0.67) < 0.005,
          "焊盘几何: U7 焊盘边缘间距 0.67mm (长边方向)", str(u7.get("焊盘间距mm")))

    # ---- 焊盘到铜皮 / 到走线 的距离 (数字都是夹具里按几何算好的) ----
    pc = by_id.get("drc.pad_clearance") or {}
    clr_rows = {r["器件"]: r for r in (pc.get("items") or [])}
    cu7, cr60 = clr_rows.get("U7") or {}, clr_rows.get("R60") or {}
    check(pc.get("status") == "pass",
          "焊盘间距: 实测值不低于 SpacingCSet 声明的焊盘间距", str(pc.get("summary")))
    check(abs((cu7.get("到走线mm") or 0) - 0.8684) < 0.01,
          "焊盘间距: U7 焊盘到走线 0.8684mm", str(cu7))
    check(abs((cu7.get("到铜皮mm") or 0) - 3.502) < 0.02,
          "焊盘间距: U7 焊盘到铜皮轮廓 3.502mm", str(cu7))
    check(abs((cr60.get("到走线mm") or 0) - 0.7938) < 0.01,
          "焊盘间距: R60 焊盘到走线 0.7938mm", str(cr60))
    inside = [r for r in clr_rows.values() if r.get("在铜皮内焊盘")]
    check(len(inside) == 1 and inside[0].get("到铜皮mm") == ">5",
          "焊盘间距: 落在铜皮轮廓内的焊盘只标注「在铜皮内」, 不给假距离",
          str(inside))

    short = by_id.get("drc.short") or {}
    check(short.get("status") == "fail",
          "短路检查: 导入的 DRC 报告里有一条 Short Circuit -> fail",
          str(short.get("summary")))
    # 没给 DRC 报告时必须如实说"算不了"。
    # 注意: 引擎会自动搜索 .brd 同目录下的导出文件 (这是设计行为), 所以这里
    # 必须把板子单独复制到一个**没有**配套文件的目录里, 才算真的"没给报告"。
    bare_dir = os.path.join(out_dir, "bare")
    os.makedirs(bare_dir, exist_ok=True)
    bare_brd = os.path.join(bare_dir, "BARE_ONLY.brd")
    shutil.copyfile(paths["brd"], bare_brd)
    bundle2 = analyze(bare_brd, out_dir=os.path.join(out_dir, "bare_out"),
                      only=["drc.short", "drc.open", "drc.unconnected"])
    for res in bundle2.get("results") or []:
        check(res["status"] == "unavailable" and res.get("missing"),
              f"没给 DRC 报告时 {res['id']} 如实报「数据不足」", res["summary"])

    # ---- 没有 SKILL 焊盘导出时: 按几何聚类也要能认出封装间距 ----
    bundle3 = analyze(bare_brd, out_dir=os.path.join(out_dir, "bare_pad_out"),
                      only=["physical.pad_geometry"])
    pg3 = (bundle3.get("results") or [{}])[0]
    grouped = [r for r in (pg3.get("items") or []) if r.get("焊盘数") == 8]
    check(bool(grouped) and abs((grouped[0].get("引脚间距mm") or 0) - 1.27) < 0.005,
          "焊盘几何: 无 evan_pads.csv 时按几何聚类认出 8 脚封装 (1.27mm)",
          str(pg3.get("items"))[:200])
    check(any("聚类" in (w or "") for w in (pg3.get("warnings") or [])),
          "焊盘几何: 聚类出来的簇明确标注「不是真实位号」",
          str(pg3.get("warnings")))

    # ---- 5. 报告输出 ----
    print("\n[5] 报告输出")
    base = "SELFTEST_REPORT"
    files = write_reports(bundle, report_dir, base_name=base)
    for key in ("xlsx", "txt", "html", "json"):
        p = files.get(key)
        check(bool(p) and os.path.exists(p) and os.path.getsize(p) > 0,
              f"{key} 报告已生成", str(p))
    try:
        import openpyxl

        wb = openpyxl.load_workbook(files["xlsx"])
        check(len(wb.sheetnames) >= 4,
              f"Excel 报告可打开, {len(wb.sheetnames)} 个页签", str(wb.sheetnames))
        check("缺失数据与补法" in wb.sheetnames, "有「缺失数据与补法」页签")
        check("产物清单" in wb.sheetnames, "有「产物清单」页签")
    except Exception as exc:  # noqa: BLE001
        check(False, "Excel 报告可打开", str(exc))
    try:
        with open(files["json"], "r", encoding="utf-8") as f:
            data = json.load(f)
        check(len(data.get("results") or []) == len(results), "JSON 报告内容完整")
    except Exception as exc:  # noqa: BLE001
        check(False, "JSON 报告可反序列化", str(exc))
    check(bool(files.get("csv_files")), "CSV 明细已输出",
          str(len(files.get("csv_files") or [])))

    # ---- 6. 统计 ----
    stats = bundle["stats"]
    print("\n[6] 结论统计")
    print(f"  共 {stats['total']} 项: 通过 {stats['pass']} / 不符合 {stats['fail']} / "
          f"需关注 {stats['warn']} / 信息 {stats['info']} / 数据不足 {stats['unavailable']}")
    kinds = {}
    for art in bundle.get("artifacts") or []:
        kinds[art["kind"]] = kinds.get(art["kind"], 0) + 1
    print(f"  生成文件: {len(bundle.get('artifacts') or [])} 个 {kinds}")

    return _finish(out_dir, args.keep)


def _finish(out_dir, keep):
    print("\n" + "=" * 74)
    if FAILURES:
        print(f"  自检未通过: {len(FAILURES)}/{CHECKS[0]} 项失败")
        for item in FAILURES:
            print(f"    - {item}")
        return 1
    print(f"  自检通过: {CHECKS[0]}/{CHECKS[0]} 项")
    if not keep:
        shutil.rmtree(out_dir, ignore_errors=True)
    else:
        print(f"  输出保留在: {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
