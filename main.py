"""
BRD-AI: 主入口
Cadence Allegro PCB Constraint 自动生成系统
=============================================
用法:
    python main.py                          # 使用默认参数
    python main.py --brd path/to/file.brd   # 指定BRD文件
    python main.py --capability advanced    # 使用高级制程能力
    python main.py --demo                   # 使用内置演示数据运行
"""
import argparse #导入 argparse 模块用于解析命令行参数
import json 
import os    # 系统模块，用于文件路径操作
import sys # 用于路径和系统操作
from datetime import datetime 

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.pcb_reader import extract_brd_info  # 导入PCB信息提取模块
from src.signal_classifier import classify_and_export
from src.rule_engine import compute_class_constraints
from src.dfm_engine import validate_constraints
from src.skill_generator import generate_skill_script
from src.report_generator import generate_excel_report, generate_text_report
from src.checklist_reader import read_checklist_excel
from src.checklist_auto_fill import auto_fill_checklist


DEMO_NETS = [
    "24V", "24V_BEAD", "24V_CMC", "24V_MB",
    "3V3_MB", "3V3_PSE",
    "GND", "GND_BEAD", "GND_CMC", "GND_MB", "AGND", "GND1", "GND2",
    "CH1_GATE", "CH1_SEN", "CH2_GATE", "CH2_SEN",
    "CH3_GATE", "CH3_SEN", "CH4_GATE", "CH4_SEN",
    "CH5_GATE", "CH5_SEN", "CH6_GATE", "CH6_SEN",
    "CH7_GATE", "CH7_SEN", "CH8_GATE", "CH8_SEN",
    "CLK_25MHZ", "CLK_25MHZ_N",
    "RESET_N", "POR_B",
    "SPI_MISO", "SPI_MOSI", "SPI_SCLK", "SPI_CS_N",
    "I2C_SDA", "I2C_SCL",
    "ETH_MDI0_P", "ETH_MDI0_N", "ETH_MDI1_P", "ETH_MDI1_N",
    "ETH_MDI2_P", "ETH_MDI2_N", "ETH_MDI3_P", "ETH_MDI3_N",
    "ETH_RGMII_TX0", "ETH_RGMII_TX1", "ETH_RGMII_TX2", "ETH_RGMII_TX3",
    "ETH_RGMII_RX0", "ETH_RGMII_RX1", "ETH_RGMII_RX2", "ETH_RGMII_RX3",
    "ETH_RGMII_TXC", "ETH_RGMII_RXC",
    "ETH_RGMII_TX_CTL", "ETH_RGMII_RX_CTL",
    "LED_STATUS", "LED_FAULT",
    "JTAG_TMS", "JTAG_TCK", "JTAG_TDI", "JTAG_TDO", "JTAG_TRST",
    "UART_TX", "UART_RX",
    "SW1", "SW2", "SW3", "SW4",
    "TP1", "TP2", "TP3", "TP4", "TP5", "TP6",
    "VBUS", "VBUS_DET",
    "CANH", "CANL",
]


def _app_dir() -> str:
    """程序所在目录: 打包后是 exe 目录 (而非临时解压目录), 外层输出默认落在这里。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def run_pipeline(
    brd_path: str = None,
    use_demo: bool = False,
    capability_level: str = "standard",
    output_dir: str = None,
    checklist_path: str = None,
    autofill_path: str = None,
    checker_name: str = "BRD-AI",
    board_type: str = "rigid",
    copper_um: float = 35.0,
    copper_oz: float = None,
    factory: str = "hongban",
) -> dict:
    base_dir = _app_dir()
    if output_dir is None:
        output_dir = os.path.join(base_dir, "output")

    print("=" * 60)
    print("  BRD-AI: Cadence Allegro Constraint Generator")
    print("=" * 60)
    print(f"  Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)
    print()

    # ---- Step 1: 读取PCB信息 ----
    print("[1/6] 读取PCB数据...")
    if use_demo or not brd_path:
        if use_demo:
            print("  -> 使用内置演示数据 (PSE Board)")
        board_info = {
            "board_name": "860-000776_PSE_Board",
            "file_path": "(demo)",
            "file_size_mb": 0,
            "nets": DEMO_NETS,
            "net_count": len(DEMO_NETS),
            "layers": [
                {"name": "TOP", "type": "signal"},
                {"name": "L2_GND", "type": "plane"},
                {"name": "L3_SIG", "type": "signal"},
                {"name": "L4_GND", "type": "plane"},
                {"name": "L5_PWR", "type": "plane"},
                {"name": "L6_SIG", "type": "signal"},
                {"name": "L7_GND", "type": "plane"},
                {"name": "BOTTOM", "type": "signal"},
            ],
            "layer_count": 8,
            "power_nets": ["24V", "24V_BEAD", "24V_CMC", "24V_MB", "3V3_MB", "3V3_PSE", "GND", "GND_BEAD", "GND_CMC", "GND_MB", "AGND", "GND1", "GND2"],
            "diff_pairs": [],
        }
    else:
        board_info = extract_brd_info(brd_path)
        if "error" in board_info:
            print(f"  [ERROR] {board_info['error']}")
            return board_info
    print(f"  -> Nets: {board_info['net_count']}, Layers: {board_info['layer_count']}")
    print()

    # ---- Step 2: 信号分类 ----
    print("[2/6] 信号自动分类...")
    classification = classify_and_export(board_info.get("nets", []))
    print(f"  -> Net Classes: {classification['total_classes']}")
    print(f"  -> Diff Pairs: {classification['total_diff_pairs']}")
    for cls_name, count in sorted(classification["summary"].items()):
        print(f"     {cls_name:15s} {count:4d} nets")
    print()
    if checklist_path and os.path.exists(checklist_path):
        print(f"[2.5/6] 读取Checklist Excel: {os.path.basename(checklist_path)}...")
        checklist_data = read_checklist_excel(checklist_path)
        print(f"  -> Items: {checklist_data['total_items']}, Rules extracted: {checklist_data['total_rules_extracted']}")
        print()

    # ---- Step 3: 规则计算 ----
    print("[3/6] 计算约束规则...")
    constraints = compute_class_constraints(classification)
    print(f"  -> Physical: {len(constraints['physical_constraints'])}")
    print(f"  -> Spacing: {len(constraints['spacing_constraints'])}")
    print(f"  -> Electrical: {len(constraints['electrical_constraints'])}")
    print(f"  -> Via: {len(constraints['via_constraints'])}")
    print()

    # ---- Step 4: DFM校验 ----
    from src.dfm_engine import normalize_level, level_label, list_levels, normalize_factory, factory_label
    factory = normalize_factory(factory)
    if capability_level not in list_levels(board_type, factory=factory):
        capability_level = normalize_level(capability_level)
    if copper_oz is None:
        copper_oz = max(1.0, round(copper_um / 35.0, 1))
    print(f"[4/6] DFM制程能力校验 ({factory_label(factory)} {board_type}-{level_label(board_type, capability_level, factory=factory)}, 铜厚 {copper_um}um)...")
    dfm_result = validate_constraints(
        constraints, capability_level,
        board_type=board_type, copper_um=copper_um, copper_oz=copper_oz,
        factory=factory,
    )
    status = "PASS" if dfm_result["passed"] else "FAIL"
    print(f"  -> Result: {status}")
    cap_sum = dfm_result.get("capability_summary", {})
    if cap_sum:
        print(f"  -> 能力: L/S={cap_sum.get('min_line_spacing')}, "
              f"最小钻孔={cap_sum.get('min_mech_drill')}mm, "
              f"AR={cap_sum.get('pth_aspect_ratio')}:1")
    print(f"  -> Errors: {dfm_result['error_count']}, Warnings: {dfm_result['warning_count']}")
    if dfm_result["errors"]:
        for err in dfm_result["errors"]:
            print(f"     [ERROR] {err}")
    if dfm_result["warnings"]:
        for warn in dfm_result["warnings"]:
            print(f"     [WARN]  {warn}")
    print()

    # ---- Step 5: 生成SKILL脚本 ----
    print("[5/6] 生成SKILL脚本...")
    skill_script = generate_skill_script(
        constraints=constraints,
        signal_classification=classification,
        board_name=board_info.get("board_name", "Untitled"),
        capability_level=capability_level,
        output_path=os.path.join(output_dir, "auto_constraint.il"),
    )
    print(f"  -> Output: {os.path.join(output_dir, 'auto_constraint.il')}")
    print(f"  -> Size: {len(skill_script)} chars")
    print()

    # ---- Step 6: 生成报告 ----
    print("[6/6] 生成报告...")
    excel_path = generate_excel_report(
        constraints=constraints,
        signal_classification=classification,
        dfm_result=dfm_result,
        board_info=board_info,
        output_path=os.path.join(output_dir, "constraint_report.xlsx"),
    )
    print(f"  -> Excel: {excel_path}")

    text_path = generate_text_report(
        constraints=constraints,
        signal_classification=classification,
        dfm_result=dfm_result,
        board_info=board_info,
        output_path=os.path.join(output_dir, "constraint_report.txt"),
    )
    print(f"  -> Text:  {text_path}")

    json_path = os.path.join(output_dir, "constraint_data.json")
    os.makedirs(output_dir, exist_ok=True)
    export_data = {
        "board_info": {k: v for k, v in board_info.items() if k not in ("nets",)},
        "classification": classification,
        "constraints": {
            "physical": [
                {k: v for k, v in c.items() if k != "nets"}
                for c in constraints.get("physical_constraints", [])
            ],
            "spacing": constraints.get("spacing_constraints", []),
            "electrical": constraints.get("electrical_constraints", []),
            "via": constraints.get("via_constraints", []),
        },
        "dfm": dfm_result,
    }
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(export_data, f, indent=2, ensure_ascii=False, default=str)
    print(f"  -> JSON:  {json_path}")

    print()
    print("=" * 60)
    print("  Execution Complete!")
    print("=" * 60)
    print()
    print("  Next Steps:")
    print(f"  1. Review: {os.path.join(output_dir, 'constraint_report.xlsx')}")
    print(f"  2. Load SKILL in Allegro: skill load \"{os.path.join(output_dir, 'auto_constraint.il')}\"")
    print()

    # ---- Step 7: 自动填写Checklist (可选) ----
    if checklist_path and os.path.exists(checklist_path):
        print("[7/7] 自动填写Checklist...")
        filled_path = os.path.join(output_dir, os.path.basename(checklist_path).replace(".xlsx", "_filled.xlsx"))
        target_path = autofill_path if autofill_path else filled_path
        output_checklist, stats = auto_fill_checklist(
            checklist_path=checklist_path,
            brd_info=board_info,
            classification=classification,
            constraints=constraints,
            dfm_result=dfm_result,
            output_path=target_path,
            checker_name=checker_name,
        )
        print(f"  -> Output: {output_checklist}")
        print(f"  -> Auto PASS: {stats['auto_pass']}, Manual: {stats['manual']}, N/A: {stats['na']}")
        print(f"  -> Auto Rate: {stats['auto_pass']/max(stats['total'],1)*100:.1f}%")
        print()

    return {
        "board_info": board_info,
        "classification": classification,
        "constraints": constraints,
        "dfm": dfm_result,
        "output_dir": output_dir,
    }


def main():
    parser = argparse.ArgumentParser(
        description="BRD-AI: Cadence Allegro PCB Constraint Generator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py --demo
  python main.py --brd "../860-000776_FAB_BRD_Rev_A_260721_3D.brd"
  python main.py --brd my_board.brd --capability advanced
  python main.py --brd my_board.brd --output ./my_output
        """,
    )
    parser.add_argument(
        "--brd", type=str, default=None,
        help="Path to Cadence Allegro .brd file"
    )
    parser.add_argument(
        "--demo", action="store_true",
        help="Use built-in demo data (PSE Board)"
    )
    parser.add_argument(
        "--capability", type=str, default="standard",
        choices=["standard", "automotive", "extreme", "advanced"],
        help="制程能力等级: standard(常规)/automotive(车规)/extreme(极限); advanced为旧版兼容"
    )
    parser.add_argument(
        "--board-type", type=str, default="rigid", dest="board_type",
        choices=["rigid", "flex"],
        help="板类型: rigid(硬板)/flex(软板及软硬结合板)"
    )
    parser.add_argument(
        "--copper", type=float, default=35.0,
        help="成品铜厚 (um), 用于线宽线距能力分档判断 (默认 35)"
    )
    parser.add_argument(
        "--copper-oz", type=float, default=None, dest="copper_oz",
        help="成品铜厚 (oz), 用于阻焊桥能力分档判断 (默认按 --copper 推算)"
    )
    parser.add_argument(
        "--output", type=str, default=None,
        help="Output directory for generated files"
    )
    parser.add_argument(
        "--nets", type=str, default=None,
        help="Path to a JSON file containing a list of net names (alternative to --brd)"
    )
    parser.add_argument(
        "--checklist", type=str, default=None,
        help="Path to Layout Checklist Excel file (.xlsx)"
    )
    parser.add_argument(
        "--gui", action="store_true",
        help="Launch graphical user interface"
    )
    parser.add_argument(
        "--autofill", type=str, default=None,
        help="Auto-fill checklist Excel: specify output path for filled checklist"
    )
    parser.add_argument(
        "--checker", type=str, default="BRD-AI",
        help="Checker name for auto-fill (default: BRD-AI)"
    )
    parser.add_argument(
        "--dfm", type=str, default=None, metavar="DESIGN_JSON",
        help="DFM功能分析模式: 传入设计参数JSON文件，直接输出制程能力比对报告 (不生成约束)"
    )
    parser.add_argument(
        "--fill-checklist", type=str, default=None, metavar="XLSX",
        dest="fill_checklist",
        help="主流程: 用 BRD 分析结果填写 Checklist, 输出填好的 xlsx + 填写报告; 传 demo 用内置演示模板"
    )
    parser.add_argument(
        "--template-info", type=str, default=None, metavar="XLSX",
        dest="template_info",
        help="只检查 Checklist 模板结构 (表头行/列识别), 不填写"
    )
    parser.add_argument(
        "--pick", type=str, default=None, metavar="BRD",
        help="坐标定位模式: 分析 .brd 并输出不符合项的孔位/元件坐标清单 (需坐标来源)"
    )
    parser.add_argument(
        "--pick-files", nargs="+", default=None, dest="pick_files", metavar="FILE",
        help="坐标来源: SKILL 导出的 evan_*.csv / place_txt.txt / Excellon .drl"
    )
    parser.add_argument(
        "--export-skill", type=str, default=None, dest="export_skill", metavar="DIR",
        help="只生成 Allegro SKILL 坐标导出脚本到指定目录 (不分析)"
    )
    parser.add_argument(
        "--factory", type=str, default="hongban",
        choices=["hongban", "sihui"],
        help="板厂制程能力库: hongban(红版)/sihui(四会)` (默认: hongban)"
    )
    # ---- BRD 分析与输出引擎 (8 大类能力) ----
    parser.add_argument(
        "--analyze", type=str, default=None, metavar="BRD",
        help="BRD 分析与输出: 按 8 大类跑全部分析能力 (DRC/物理结构/SI电气/"
             "制造输出/报告/装配/MCAD/数据导出), 并生成报告与可生成的制造文件"
    )
    parser.add_argument(
        "--analyze-files", nargs="+", default=None, dest="analyze_files", metavar="FILE",
        help="分析用的 Allegro 导出文件: DRC 报告(.rpt) / Placement(place_txt.txt) / "
             "Excellon(.drl) / 网表(pstxnet.dat) / IPC-356(.ipc) / 另一个 .brd(做版本差异)"
    )
    parser.add_argument(
        "--analyze-only", nargs="+", default=None, dest="analyze_only", metavar="ID",
        help="只跑指定的能力 id 或类别 (如 drc.width / mfg.artwork / reports)"
    )
    parser.add_argument(
        "--analyze-skip", nargs="+", default=None, dest="analyze_skip", metavar="ID",
        help="跳过指定的能力 id 或类别"
    )
    parser.add_argument(
        "--analyze-formats", type=str, default="xlsx,txt,html,json,csv",
        dest="analyze_formats",
        help="报告输出格式, 逗号分隔: xlsx,txt,html,json,csv (默认全出)"
    )
    parser.add_argument(
        "--list-capabilities", action="store_true", dest="list_capabilities",
        help="列出 BRD 分析与输出引擎支持的全部能力 (8 大类) 后退出"
    )
    parser.add_argument(
        "--input-project", metavar="BRD",
        help="以 BRD 为主资料创建统一输入模型并输出 JSON"
    )
    parser.add_argument(
        "--supplements", nargs="*", default=[], metavar="FILE",
        help="统一输入模型的补充资料: Gerber / BOM CSV / DSN / ODB++ / STEP"
    )
    parser.add_argument(
        "--input-json", metavar="JSON", default=None,
        help="统一输入模型 JSON 输出路径 (默认输出到标准输出)"
    )

    args = parser.parse_args()

    if args.input_project:
        import json
        from src.design_inputs import load_project
        try:
            project = load_project(args.input_project, args.supplements)
            content = json.dumps(project, ensure_ascii=False, indent=2)
            if args.input_json:
                with open(args.input_json, "w", encoding="utf-8") as f:
                    f.write(content + "\n")
                print("统一输入模型已写入:", args.input_json)
            else:
                print(content)
            if any(item["status"] == "error" for item in project["supplements"]):
                raise SystemExit(1)
        except (OSError, ValueError) as exc:
            parser.exit(1, "输入资料解析失败: " + str(exc) + "\n")
        return

    if args.list_capabilities:
        from src.brd_analysis import capability_rows

        rows = capability_rows()
        print("=" * 78)
        print(f"  BRD 分析与输出能力目录 (共 {len(rows)} 项)")
        print("=" * 78)
        current = None
        for row in rows:
            if row["category_label"] != current:
                current = row["category_label"]
                print()
                print(f"  【{current}】")
            print(f"    {row['id']:<26} {row['name_cn']}")
            print(f"        {row['desc']}")
            print(f"        数据来源: {row['engine_label']}   需要: "
                  f"{'、'.join(row['needs']) or '—'}")
        return

    if args.analyze:
        from src.brd_analysis import analyze, write_reports

        if not os.path.exists(args.analyze):
            print(f"[ERROR] BRD 文件不存在: {args.analyze}")
            sys.exit(1)
        out_dir = args.output or os.path.join(_app_dir(), "output")
        print("=" * 78)
        print("  BRD 分析与输出 (DRC / 物理结构 / SI电气 / 制造输出 / 报告 / 装配 / MCAD / 数据导出)")
        print("=" * 78)
        print(f"  文件: {args.analyze}")
        if args.analyze_files:
            print(f"  导出文件: {'; '.join(os.path.basename(p) for p in args.analyze_files)}")
        print("-" * 78)
        bundle = analyze(
            args.analyze, out_dir=out_dir, export_files=args.analyze_files,
            only=args.analyze_only, skip=args.analyze_skip,
            log=lambda text, tag=None: print(text),
        )
        if not bundle.get("ok"):
            print(f"[ERROR] {bundle.get('error')}")
            sys.exit(1)

        board = bundle["board"]
        print("-" * 78)
        print(f"  板名: {board.get('board_name')}   层数: {board.get('layer_count')}   "
              f"网络: {board.get('net_count')}   器件: {board.get('part_count')}")
        stats = bundle["stats"]
        print(f"  结论: 通过 {stats['pass']} / 不符合 {stats['fail']} / 需关注 {stats['warn']} / "
              f"信息 {stats['info']} / 数据不足 {stats['unavailable']}  (共 {stats['total']} 项)")
        for res in bundle["results"]:
            if res["status"] in ("fail", "warn"):
                print(f"    [{res['status_label']}] {res['name_cn']}: {res['summary']}")
        if bundle.get("catalog_errors"):
            print("  [WARN] 部分类别模块未加载:")
            for err in bundle["catalog_errors"]:
                print(f"        {err}")

        formats = [f.strip() for f in (args.analyze_formats or "").split(",") if f.strip()]
        base = f"BRD分析报告_{board.get('board_name') or 'board'}"
        files = write_reports(bundle, out_dir, base_name=base, formats=formats or None)
        print("-" * 78)
        print("  报告:")
        for key in ("xlsx", "txt", "html", "json"):
            if files.get(key):
                print(f"    {key.upper():<5} {files[key]}")
        if files.get("csv"):
            print(f"    CSV   {files['csv']} 目录 ({len(files.get('csv_files') or [])} 个文件)")
        if bundle.get("artifacts"):
            print("  生成的设计/制造文件:")
            for art in bundle["artifacts"][:40]:
                print(f"    [{art['kind']:<9}] {art['path']}")
            if len(bundle["artifacts"]) > 40:
                print(f"    ... 另有 {len(bundle['artifacts']) - 40} 个")
        missing = [r for r in bundle["results"] if r.get("missing")]
        if missing:
            print("-" * 78)
            print(f"  数据不足的 {len(missing)} 项 (缺什么 / 怎么补, 详见报告「缺失数据与补法」页):")
            for res in missing[:12]:
                print(f"    {res['name_cn']}: {res['missing'][0]}")
        return

    if args.export_skill:
        from src.allegro_skill import write_bundle

        paths = write_bundle(args.export_skill)
        print("已生成 SKILL 坐标导出脚本:")
        for key in ("il", "scr", "readme"):
            print("  " + paths[key])
        print('PCB Editor 里执行: skill load("%s")  然后  evan_pick_all("%s")'
              % (paths["il"].replace("\\", "/"),
                 args.export_skill.replace("\\", "/")))
        return

    if args.pick:
        from src.pick import analyze_and_locate, find_sources, print_summary

        files = args.pick_files or find_sources(args.pick, args.output or "")
        bundle = analyze_and_locate(args.pick, files=files, out_dir=args.output,
                                    board_type=args.board_type,
                                    level=args.capability, factory=args.factory)
        print_summary(bundle)
        return

    if args.dfm:
        from src.dfm_engine import analyze_design, factory_label, normalize_factory
        dfm_factory = normalize_factory(args.factory)
        if args.dfm == "template":
            template = {
                "board_size": [100, 80],
                "min_line_width": 0.10, "min_spacing": 0.10,
                "copper_um": 35, "copper_oz": 1.0,
                "min_mech_drill": 0.2, "max_mech_drill": 3.2,
                "min_laser_via": 0.1, "min_annular_ring": 0.075,
                "board_thickness": 1.6,
                "pth_to_pth_spacing": 0.3,
                "copper_to_edge_inner": 0.25, "copper_to_edge_outer": 0.2,
                "npth_to_edge": 0.5,
                "solder_mask_bridge": 0.1, "solder_mask_opening": 0.1,
                "silkscreen_width": 0.15, "silkscreen_clearance": 0.15,
                "plug_hole_diameter": 0.3, "plug_board_thickness": 1.6,
                "test_pad_2wire": 0.4,
                "coverlay_spacing": 0.5, "coverlay_to_pad": 0.3,
                "coverlay_to_trace": 0.15, "coverlay_window": [0.6, 0.6],
            }
            print(json.dumps(template, indent=2, ensure_ascii=False))
            return
        if not os.path.exists(args.dfm):
            print(f"[ERROR] 设计参数文件不存在: {args.dfm} (可用 --dfm template 生成模板)")
            sys.exit(1)
        with open(args.dfm, "r", encoding="utf-8-sig") as f:
            design = json.load(f)
        result = analyze_design(design, board_type=args.board_type,
                                capability_level=args.capability,
                                factory=dfm_factory)
        print("=" * 70)
        print(f"  DFM制程能力分析报告: {factory_label(dfm_factory)} - {result['board_type_label']}"
              f" - {result['capability_label']} (铜厚 {result['copper_um']}um)")
        print("=" * 70)
        status_icon = {"pass": "[PASS]", "warn_limit": "[WARN]", "fail": "[FAIL]", "info": "[INFO]"}
        for it in result["items"]:
            print(f"  {status_icon[it['status']]} {it['message']}")
            if it.get("note"):
                print(f"          备注: {it['note']}")
        print("-" * 70)
        print(f"  结论: {'DFM PASS' if result['passed'] else 'DFM FAIL'}"
              f"  (错误 {result['error_count']}, 警告 {result['warning_count']})")
        coverage = result.get("coverage") or {}
        if coverage:
            print(f"  数据覆盖率: {coverage.get('ratio', 0):.0%} "
                  f"({coverage.get('checked', 0)}/{coverage.get('total', 0)})"
                  f"  判定: {result.get('verdict', '')}"
                  f"  可否放行: {'可以' if result.get('releasable') else '不可以'}")
            if coverage.get("missing_items"):
                print(f"  缺数据未检查: {'、'.join(coverage['missing_items'])}")
        out_dir = args.output or os.path.join(_app_dir(), "output")
        os.makedirs(out_dir, exist_ok=True)
        json_out = os.path.join(out_dir, "dfm_analysis.json")
        with open(json_out, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, ensure_ascii=False, default=str)
        print(f"  详细报告(JSON): {json_out}")

        from src.dfm_report import generate_dfm_excel, generate_dfm_text
        board_meta = design.get("board_meta", {}) if isinstance(design, dict) else {}
        if isinstance(design.get("board_name"), str):
            board_meta.setdefault("board_name", design.get("board_name"))
        xlsx_out = generate_dfm_excel(result, os.path.join(out_dir, "dfm_report.xlsx"), board_meta)
        txt_out = generate_dfm_text(result, os.path.join(out_dir, "dfm_report.txt"), board_meta)
        print(f"  DFM报告(Excel): {xlsx_out}")
        print(f"  DFM报告(Text):  {txt_out}")
        risk = result.get("risk", {})
        if risk:
            print(f"  可制造性评分: {risk.get('score')}/100  风险等级: {risk.get('risk_level')}")
        return

    if args.template_info:
        from src.checklist_template import read_template
        if not os.path.exists(args.template_info):
            print(f"[ERROR] 文件不存在: {args.template_info}")
            sys.exit(1)
        info = read_template(args.template_info)
        names = {"category": "类别", "subcategory": "子类", "no": "编号", "grade": "级别",
                 "items": "条目", "standard": "符合标准", "note": "原因",
                 "check": "检查", "approved": "签核"}
        print("=" * 66)
        print("  Checklist 模板检查")
        print("=" * 66)
        print(f"  文件    : {info['path']}")
        print(f"  工作表  : {info['sheet']}")
        print(f"  表头行  : 第 {info['header_row']} 行")
        print(f"  识别方式: {info['method']}")
        for key, col in sorted(info["columns"].items(), key=lambda kv: kv[1]):
            print(f"    {names.get(key, key):<6} 第 {col} 列")
        print(f"  检查项  : {info['count']} 项 (带编号 {info['numbered']} 项)")
        print(f"  规则库  : {info['rule_count']} 条 ({info['config_path'] or '内置默认'})")
        if info["warnings"]:
            print("  警告:")
            for warn in info["warnings"]:
                print(f"    ! {warn}")
        else:
            print("  结论    : 模板结构正常")
        return

    if args.fill_checklist:
        from src.checklist_pipeline import run as run_checklist
        checklist = args.fill_checklist
        demo = checklist.lower() == "demo"
        if not demo and not os.path.exists(checklist):
            print(f"[ERROR] Checklist 文件不存在: {checklist}")
            sys.exit(1)
        out_dir = args.output or os.path.join(_app_dir(), "output")
        result = run_checklist(
            checklist_path="" if demo else checklist,
            output_dir=out_dir,
            brd_path=args.brd or "",
            nets_path=args.nets or "",
            factory=args.factory,
            board_type=args.board_type,
            level=args.capability,
            copper_um=args.copper,
            copper_oz=args.copper_oz if args.copper_oz is not None else 1.0,
            checker=args.checker,
            demo=demo or (not args.brd and not args.nets),
            log=lambda text, tag=None: print(text),
        )
        stats = result["stats"]
        print("=" * 66)
        print(f"  填写结果: {result['files']['filled']}")
        print(f"  填写报告: {result['files']['report_xlsx']}")
        print(f"  文本摘要: {result['files']['report_txt']}")
        print(f"  统计: 共 {stats['total']} 项, PASS {stats['auto_pass']}, "
              f"VERIFY {stats['auto_verify']}, FAIL {stats['auto_fail']}, "
              f"MANUAL {stats['manual']}, N/A {stats['na']}")
        return

    if args.gui:
        from gui import main as gui_main
        gui_main()
        return

    if args.brd and args.nets:
        print("[ERROR] --brd and --nets are mutually exclusive")
        sys.exit(1)

    if not args.brd and not args.demo and not args.nets:
        print("[INFO] No input specified. Using --demo mode.")
        args.demo = True

    if args.nets:
        with open(args.nets, "r", encoding="utf-8") as f:
            net_list = json.load(f)
        board_info = {
            "board_name": "Imported Nets",
            "file_path": args.nets,
            "file_size_mb": 0,
            "nets": net_list,
            "net_count": len(net_list),
            "layers": [],
            "layer_count": 0,
            "power_nets": [],
            "diff_pairs": [],
        }
        classification = classify_and_export(net_list)
        constraints = compute_class_constraints(classification)
        copper_oz = args.copper_oz if args.copper_oz else max(1.0, round(args.copper / 35.0, 1))
        dfm_result = validate_constraints(
            constraints, args.capability,
            board_type=args.board_type, copper_um=args.copper, copper_oz=copper_oz,
            factory=args.factory,
        )

        output_dir = args.output or os.path.join(_app_dir(), "output")
        generate_skill_script(
            constraints=constraints,
            signal_classification=classification,
            board_name=board_info["board_name"],
            capability_level=args.capability,
            output_path=os.path.join(output_dir, "auto_constraint.il"),
        )
        generate_excel_report(
            constraints=constraints,
            signal_classification=classification,
            dfm_result=dfm_result,
            board_info=board_info,
            output_path=os.path.join(output_dir, "constraint_report.xlsx"),
        )
        generate_text_report(
            constraints=constraints,
            signal_classification=classification,
            dfm_result=dfm_result,
            board_info=board_info,
            output_path=os.path.join(output_dir, "constraint_report.txt"),
        )
        print("[DONE]")
        return

    run_pipeline(
        brd_path=args.brd,
        use_demo=args.demo,
        capability_level=args.capability,
        output_dir=args.output,
        checklist_path=args.checklist,
        autofill_path=args.autofill,
        checker_name=args.checker,
        board_type=args.board_type,
        copper_um=args.copper,
        copper_oz=args.copper_oz,
        factory=args.factory,
    )


if __name__ == "__main__":
    main()
