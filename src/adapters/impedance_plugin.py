"""Generate portable native IMP_HIGHLIGHT bundles; never launch a viewer as CAD."""
import json
import math
import os
import shutil
import sys
import uuid
from pathlib import Path


def quote_skill(value):
    text = str(value)
    if any(ord(c) < 32 for c in text):
        raise ValueError("参数不能含控制字符")
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def write_plugin(folder):
    folder = Path(folder).resolve()
    folder.mkdir(parents=True, exist_ok=True)
    source = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2])) / "skill"
    if any(folder.iterdir()):
        raise ValueError("工具目录非空，请选择新的输出目录，避免覆盖插件或板数据")
    for name in ("imp_highlight.il", "imp_highlight.form"):
        target = folder / name
        if target.exists():
            raise ValueError("工具目录非空，请选择新的输出目录")
    for name in ("imp_highlight.il", "imp_highlight.form"):
        shutil.copy2(source / name, folder / name)
    token = uuid.uuid4().hex
    (folder / "session.json").write_text(json.dumps({"token": token}, indent=2), encoding="utf-8")
    prepare_load_script(folder)
    # No Python needed on the user's machine. Resolve the extracted directory
    # at runtime, not the developer's original delivery path.
    batch = ('@echo off\nsetlocal\nset "IMP_PLUGIN_DIR=%~dp0"\n'
             'powershell.exe -NoProfile -Command "'
             "$d=$env:IMP_PLUGIN_DIR.TrimEnd([char]92).Replace([char]92,[char]47); "
             "$q=[char]34; $esc=[string][char]92+$q; $d=$d.Replace($q,$esc); "
             "$s='skill load('+$q+$d+'/imp_highlight.il'+$q+')'+[Environment]::NewLine; "
             f"$s+='skill imp_setup('+$q+$d+$q+' '+$q+'{token}'+$q+')'+[Environment]::NewLine; "
             "$s+='IMP_HIGHLIGHT'+[Environment]::NewLine; "
             "[IO.File]::WriteAllText((Join-Path $env:IMP_PLUGIN_DIR 'load_imp.scr'),$s,[Text.Encoding]::Default)\"\n"
             'if errorlevel 1 (echo Failed to prepare script. & pause & exit /b 1)\n'
             'echo Ready. In Allegro PCB Editor: File - Script - load_imp.scr\npause\n')
    (folder / "prepare_load.cmd").write_text(batch, encoding="ascii")
    (folder / "README.txt").write_text(
        "IMP_HIGHLIGHT 原生插件\n解压/移动本目录后，先双击 prepare_load.cmd，\n"
        "然后在有许可的 PCB Editor 打开 BRD，通过 File > Script 运行 load_imp.scr。无需 Python。\n"
        "或在主程序选择本插件目录并点击「准备加载脚本」，命令自动复制到剪贴板。\n"
        "手动命令：\n"
        'skill load("<本目录>/imp_highlight.il")\n'
        f'skill imp_setup("<本目录>" "{token}")\nIMP_HIGHLIGHT\n'
        "仅有许可的 PCB Editor；Free Viewer 不等于可用 SKILL/Constraint Manager。\n"
        "选择真实铜层、模式、线宽/间距（mil）、调色板色块；保持原生面板打开查看临时高亮。\n"
        "Close 清除显示，不保存 BRD。详细边界见交付 PCB_TRACE_FILTER.md。\n",
        encoding="utf-8")
    return folder


def prepare_load_script(folder):
    """Repair only the loader after moving a bundle; preserve session and CSVs."""
    folder = Path(folder).resolve()
    for name in ("imp_highlight.il", "imp_highlight.form", "session.json"):
        if not (folder / name).is_file():
            raise ValueError("不是完整 IMP_HIGHLIGHT 插件目录：缺少 " + name)
    data = json.loads((folder / "session.json").read_text(encoding="utf-8-sig"))
    token = data.get("token") if isinstance(data, dict) else None
    if not isinstance(token, str) or len(token) != 32 or any(c not in "0123456789abcdef" for c in token):
        raise ValueError("插件 session.json 的会话编号无效")
    prefix = folder.as_posix()
    command = (f'skill load({quote_skill(prefix + "/imp_highlight.il")})\n'
               f'skill imp_setup({quote_skill(prefix)} {quote_skill(token)})\n'
               'IMP_HIGHLIGHT\n')
    path = folder / "load_imp.scr"
    path.write_text(command, encoding="mbcs" if os.name == "nt" else "utf-8")
    return path


def write_action(folder, *, layer, width="", spacing="", tolerance="0.01", mode="geometry",
                 net="", pair="", impedance="", color=1, board=""):
    if not layer or mode not in ("geometry", "impedance", "pair"):
        raise ValueError("请选择目标层和有效识别模式")
    for label, text in (("线宽", width), ("间距", spacing), ("目标阻抗", impedance)):
        if str(text).strip():
            number = float(text)
            if not math.isfinite(number) or number < 0:
                raise ValueError(label + "必须是非负有限数")
    number = float(tolerance)
    if not math.isfinite(number) or number < 0:
        raise ValueError("容差必须是非负有限数")
    if not isinstance(color, int) or isinstance(color, bool) or not 1 <= color <= 255:
        raise ValueError("请选择 Allegro 调色板颜色编号")
    # Python accepts "1_0" as 10 but SKILL parses it as a symbol. Send a
    # canonical numeric literal, never the original user expression.
    def literal(value):
        return format(float(value), ".15g") if str(value).strip() else ""
    width, spacing, tolerance, impedance = (literal(value) for value in (width, spacing, tolerance, impedance))
    folder = Path(folder).resolve()
    prepare_load_script(folder)
    token = json.loads((folder / "session.json").read_text(encoding="utf-8"))["token"]
    values = [layer, width, spacing, tolerance, mode, net, pair, impedance]
    text = 'skill imp_prepare(' + quote_skill(board) + ' ' + quote_skill(token) + ' '
    text += ' '.join(quote_skill(v) for v in values) + f' {color})\nIMP_HIGHLIGHT\n'
    action = folder / ("apply_" + uuid.uuid4().hex[:10] + ".scr")
    action.write_text(text, encoding="mbcs" if os.name == "nt" else "utf-8")
    return action
