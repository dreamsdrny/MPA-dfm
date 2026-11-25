"""RS-274X 基础适配器：保留中心线/闪光及孔径，不将它们误认为已布尔运算的铜面。"""
import re
from pathlib import Path

_EXT = {".gtl": "copper_top", ".gbl": "copper_bottom", ".gts": "mask_top",
        ".gbs": "mask_bottom", ".gtp": "paste_top", ".gbp": "paste_bottom",
        ".gto": "silkscreen_top", ".gbo": "silkscreen_bottom", ".gko": "outline", ".gm1": "outline"}


def _role(path, text):
    attr = re.search(r"%TF\.FileFunction,([^*%]+)", text, re.I)
    if attr:
        parts = [p.strip().lower() for p in attr.group(1).split(",")]
        first = parts[0]
        if first in ("profile", "outline"):
            return "outline", "x2_file_function"
        kind = {"copper": "copper", "soldermask": "mask", "paste": "paste", "legend": "silkscreen"}.get(first)
        if kind:
            side = "top" if "top" in parts else "bottom" if "bot" in parts or "bottom" in parts else next((p for p in parts if re.fullmatch(r"l\d+", p)), "unknown")
            return kind + "_" + side, "x2_file_function"
        return "unknown", "x2_file_function"
    role = _EXT.get(Path(path).suffix.lower())
    return (role, "extension") if role else ("unknown", "unidentified")


def parse_gerber(path):
    text = Path(path).read_text(encoding="ascii", errors="strict")
    role, role_source = _role(path, text)
    warnings, geometry, apertures = [], [], {}
    fs = re.search(r"%FS([LT])([AI])X(\d)(\d)Y(\d)(\d)\*%", text, re.I)
    mo = re.search(r"%MO(MM|IN)\*%", text, re.I)
    if not fs or not mo:
        return {"status": "unsupported", "role": role, "role_source": role_source,
                "geometry": [], "warnings": ["缺少或不支持 FS 坐标格式或 MO 单位声明"]}
    zero, notation = fs.group(1).upper(), fs.group(2).upper()
    formats = {"X": (int(fs.group(3)), int(fs.group(4))), "Y": (int(fs.group(5)), int(fs.group(6)))}
    scale = 1.0 if mo.group(1).upper() == "MM" else 25.4
    if notation != "A":
        return {"status": "unsupported", "role": role, "role_source": role_source,
                "geometry": [], "warnings": ["暂不支持增量坐标"]}

    def coord(raw, axis):
        if "." in raw:
            return float(raw) * scale
        integer, decimal = formats[axis]
        sign = -1 if raw.startswith("-") else 1
        digits = raw.lstrip("+-")
        if len(digits) > integer + decimal:
            raise ValueError("Gerber 坐标超出 FS 指定位数")
        digits = digits.ljust(integer + decimal, "0") if zero == "T" else digits
        return sign * int(digits) / 10 ** decimal * scale

    x = y = None
    aperture = None
    operation = None
    interpolation = "linear"
    polarity, region = "dark", False
    # 参数块与普通命令分开处理，避免把属性内容当作图形命令。
    for block in re.split(r"(%[^%]*%)", text.replace("\r", "").replace("\n", "")):
        if not block:
            continue
        if block.startswith("%"):
            param = block[1:-1].strip("*")
            add = re.fullmatch(r"ADD(\d+)([A-Za-z_][\w.$-]*)(?:,([^*]+))?", param)
            if add:
                code, shape, params = add.groups()
                try:
                    dims = [float(v) * scale for v in (params or "").split("X") if v]
                except ValueError:
                    dims = []
                apertures[code] = {"shape": shape, "parameters_mm": dims}
                if shape not in ("C", "R", "O", "P"):
                    warnings.append("未展开孔径宏: D" + code)
                elif shape == "P":
                    # 多边形的顶点数和旋转角不是长度，不做错误换算。
                    raw = (params or "").split("X")
                    apertures[code]["parameters"] = raw
                    apertures[code].pop("parameters_mm", None)
                    warnings.append("多边形孔径参数保留原始值: D" + code)
            elif param.startswith("LP"):
                polarity = "clear" if param == "LPC" else "dark"
            elif not param.startswith(("FS", "MO", "TF", "TA", "TO", "TD")):
                warnings.append("未处理 Gerber 参数: " + param[:80])
            continue
        for raw in block.split("*"):
            cmd = raw.strip().upper()
            if not cmd or cmd.startswith("G04") or cmd in ("M02", "M00"):
                continue
            if cmd in ("G36", "G37"):
                region = cmd == "G36"
                continue
            if cmd in ("G74", "G75"):
                warnings.append("圆弧象限模式保留为未解析: " + cmd)
                continue
            gs = re.findall(r"G0?[123]", cmd)
            for g in gs:
                interpolation = {1: "linear", 2: "clockwise_arc", 3: "counterclockwise_arc"}[int(g[1:])]
            cmd = re.sub(r"G0?[123]", "", cmd)
            if re.fullmatch(r"D\d+", cmd) and int(cmd[1:]) >= 10:
                aperture = cmd[1:]
                continue
            match = re.fullmatch(r"(?:X[+-]?[\d.]+)?(?:Y[+-]?[\d.]+)?(?:I[+-]?[\d.]+)?(?:J[+-]?[\d.]+)?(?:D0?[123])?", cmd)
            if not cmd or not match:
                if cmd:
                    warnings.append("未处理 Gerber 命令: " + cmd[:80])
                continue
            values = dict(re.findall(r"([XYIJ])([+-]?[\d.]+)", cmd))
            d = re.search(r"D0?([123])$", cmd)
            if d:
                operation = int(d.group(1))
            nx = coord(values["X"], "X") if "X" in values else x
            ny = coord(values["Y"], "Y") if "Y" in values else y
            if not values:
                continue
            if nx is None or ny is None or operation is None:
                warnings.append("坐标或操作未初始化: " + cmd)
                x, y = nx, ny
                continue
            if operation in (1, 3):
                if operation == 1 and (x is None or y is None):
                    warnings.append("绘制起点未初始化: " + cmd)
                else:
                    item = {"type": "flash" if operation == 3 else interpolation, "end_mm": [nx, ny],
                            "aperture": aperture, "polarity": polarity, "region": region}
                    if operation == 1:
                        item["start_mm"] = [x, y]
                    if operation == 1 and interpolation != "linear":
                        item["center_offset_mm"] = [coord(values.get("I", "0"), "X"), coord(values.get("J", "0"), "Y")]
                    geometry.append(item)
            x, y = nx, ny
    if not apertures and geometry:
        warnings.append("没有孔径定义")
    return {"status": "partial" if warnings else "parsed", "role": role, "role_source": role_source,
            "unit": "mm", "apertures": apertures, "geometry": geometry, "warnings": list(dict.fromkeys(warnings))}
