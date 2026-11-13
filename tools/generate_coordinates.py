"""Generate BRD coordinate scripts or launch the editor to export them.

python tools/generate_coordinates.py --brd board.brd --out exports [--launch]
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.allegro_skill import write_bundle
from src.allegro_bridge import AllegroSession, discover_editor


def main():
    parser = argparse.ArgumentParser(description="生成 COMP_XY + 丝印/板框/焊盘坐标导出脚本")
    parser.add_argument("--brd", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--editor", default="")
    parser.add_argument("--launch", action="store_true")
    args = parser.parse_args()
    paths = write_bundle(args.out)
    print("在对应 BRD 中 File > Script 执行：", paths["coordinates_scr"])
    print("输出 COMP_XY.txt、evan_components/holes/pads/traces/silkscreen/outline.csv")
    if args.launch:
        editor = discover_editor(args.editor)
        if not editor:
            parser.error("未找到 Allegro，请用 --editor 指定 allegro.exe")
        session = AllegroSession(args.brd, editor)
        session.prepare(args.out)
        print("Allegro PID:", session.launch())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
