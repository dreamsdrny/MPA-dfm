"""Excellon 制造孔几何。孔型/跨层/对齐由明确元数据提供，不从孔径猜测。"""
import re
import warnings
from pathlib import Path

from gerbonara import ExcellonFile
from gerbonara.utils import MM
from shapely.ops import unary_union


def drill_files(folder):
    files = []
    for path in Path(folder).rglob("*"):
        if not path.is_file():
            continue
        if path.suffix.lower() in {".drl", ".xln", ".exc", ".ncd", ".tap", ".fdr", ".rou"}:
            files.append(path)
        elif path.suffix.lower() == ".txt":
            with path.open(encoding="ascii", errors="replace") as stream:
                if re.search(r"(?m)^\s*M48\s*$", stream.read(2000)):
                    files.append(path)
    return sorted(files)


def read_drill(path, metadata=None):
    metadata = {} if metadata is None else metadata
    if not isinstance(metadata, dict):
        raise ValueError("钻孔元数据必须是对象")
    plated = metadata.get("plated")
    if plated is not None and not isinstance(plated, bool):
        raise ValueError("plated 必须为 true/false")
    start, end = metadata.get("start_layer"), metadata.get("end_layer")
    if (start is None) != (end is None):
        raise ValueError("start_layer/end_layer 必须同时提供")
    if start is not None and (any(isinstance(n, bool) or not isinstance(n, int) for n in (start, end))
                              or not 1 <= start < end):
        raise ValueError("钻孔跨层要求 1 ≤ start_layer < end_layer")
    process = metadata.get("process")
    if process not in (None, "mechanical", "laser"):
        raise ValueError("process 必须为 mechanical/laser")
    alignment = metadata.get("alignment_confirmed", False)
    if not isinstance(alignment, bool):
        raise ValueError("alignment_confirmed 必须为 true/false")
    nets = metadata.get("nets", {})
    if not isinstance(nets, dict) or any(not isinstance(k, str) or not re.fullmatch(r"0|[1-9]\d*", k)
                                         or not isinstance(v, str) or not v.strip()
                                         for k, v in nets.items()):
        raise ValueError("nets 必须为逐孔索引到非空网络名的映射")
    with warnings.catch_warnings(record=True) as notices:
        warnings.simplefilter("always")
        settings = None
        if metadata.get("number_format") is not None:
            from gerbonara.cam import FileSettings
            fmt = metadata["number_format"]
            if not isinstance(fmt, (list, tuple)) or len(fmt) != 2 or any(isinstance(n, bool) or not isinstance(n, int) or not 1 <= n <= 8 for n in fmt):
                raise ValueError("number_format 需要明确整数/小数位数")
            settings = FileSettings(number_format=tuple(fmt))
        if metadata.get("native_cadence") is True:
            # Cadence's per-file Holesize numbering differs from GLOBAL Tnn.
            # The enhanced header's TnnC is authoritative. Validate redundant
            # comment sizes then omit them only from the parser input, never
            # rewrite the native file or discard machining commands.
            text = Path(path).read_text(encoding="ascii", errors="strict")
            tools = {int(m[1]): float(m[2]) for m in re.finditer(r"(?m)^T(\d+)C([\d.]+)\s*$", text)}
            def tool_comment(match):
                number, diameter = int(match[1]), float(match[2])
                if number not in tools or abs(tools[number] - diameter) > 1e-6:
                    raise ValueError("原生 NC 工具头与 Holesize 注释尺寸不一致")
                return "; Cadence redundant tool comment validated"
            text = re.sub(r"(?m)^;T(\d+)\s+Holesize\s+\d+\.\s*=\s*([\d.]+)[^\r\n]*", tool_comment, text)
            drill = ExcellonFile.from_string(text, settings=settings, plated=plated, filename=str(path))
        else:
            drill = ExcellonFile.open(path, plated=plated, settings=settings)
        holes = []
        # Lazy import avoids the Gerber orchestration module importing this parser back.
        from src.gerber_analysis import _primitive_polygon
        for index, obj in enumerate(drill.objects):
            tool = obj.aperture
            actual_plated = getattr(tool, "plated", None)
            if plated is not None and actual_plated is not None and plated != actual_plated:
                raise ValueError("元数据 plated 与 Excellon 孔工具属性冲突")
            # Gerbonara 1.6 does not populate all generic Excellon tools from
            # open(plated=...). Preserve the verified explicit fallback here.
            if actual_plated is None:
                actual_plated = plated
            shape = unary_union([_primitive_polygon(p) for p in obj.to_primitives(unit=MM)])
            if shape.is_empty:
                raise ValueError(f"孔图元 {index} 无法还原")
            diameter = float(MM(tool.diameter, tool.unit))
            if diameter <= 0:
                raise ValueError(f"孔图元 {index} 孔径无效")
            holes.append(dict(index=index, geometry=shape, diameter_mm=diameter,
                               plated=actual_plated, start_layer=start, end_layer=end,
                               process=process, alignment_confirmed=alignment,
                               net=nets.get(str(index), "").strip()))
        if any(int(k) >= len(holes) for k in nets):
            raise ValueError("nets 孔索引超出实际钻孔对象范围")
    if any(re.search(r"unknown|unsupported|unhandled|ignored", str(n.message), re.IGNORECASE) for n in notices):
        raise ValueError("Excellon 存在未处理命令，钻孔检查未完成")
    return dict(path=str(Path(path).resolve()), role="drill", holes=holes,
                warnings=[str(n.message) for n in notices], brd_alignment=metadata.get("brd_alignment") is True)
