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
import argparse
import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.pcb_reader import extract_brd_info
from src.signal_classifier import classify_and_export
from src.rule_engine import compute_class_constraints
from src.dfm_engine import validate_constraints
from src.skill_generator import generate_skill_script
from src.report_generator import generate_excel_report, generate_text_report
from src.checklist_reader import read_checklist_excel, merge_checklist_with_defaults
from src.checklist_auto_fill import auto_fill_checklist, generate_checklist_summary


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
    base_dir = os.path.dirname(os.path.abspath(__file__))
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
        "--factory", type=str, default="hongban",
        choices=["hongban", "sihui"],
        help="板厂制程能力库: hongban(红版)/sihui(四会)` (默认: hongban)"
    )

    args = parser.parse_args()

    if args.dfm:
        from src.dfm_engine import analyze_design, load_capability_db, factory_label, normalize_factory
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
        out_dir = args.output or os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")
        os.makedirs(out_dir, exist_ok=True)
        json_out = os.path.join(out_dir, "dfm_analysis.json")
        with open(json_out, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, ensure_ascii=False, default=str)
        print(f"  详细报告: {json_out}")
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

        output_dir = args.output or os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")
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