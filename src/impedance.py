"""Strict native catalogue import. No physical measurements are computed here."""
import csv
import hashlib
import io
import json
import os
from pathlib import Path

MAX_EXPORT_BYTES = 64 * 1024 * 1024


def _export_rows(folder, name, signature, required):
    folder = Path(folder).resolve()
    session = json.loads((folder / "session.json").read_text(encoding="utf-8-sig"))
    if not isinstance(session, dict) or not isinstance(session.get("token"), str):
        raise ValueError("无法读取有效插件会话记录")  # noqa: TRY004  外部文件合同统一 ValueError
    token = session["token"]
    marker_path = folder / (name + ".done")
    before = marker_path.read_bytes()
    marker = before.decode("ascii").split()
    if len(marker) != 4 or marker[:2] != [signature, token]:
        raise ValueError("原生导出未完成或不属于当前插件会话，请在 PCB Editor 重新执行")
    count = int(marker[2])
    if not 0 <= count <= 250000:
        raise ValueError("原生导出数量超限或无效")
    path = folder / (name + ".csv")
    if path.stat().st_size > MAX_EXPORT_BYTES:
        raise ValueError("原生导出文件超过安全读取上限")
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("mbcs" if os.name == "nt" else "utf-8")
    reader = csv.DictReader(io.StringIO(text))
    fields = reader.fieldnames or []
    if len(fields) != len(set(fields)) or not required.issubset(fields):
        raise ValueError("原生导出表头缺失或重复")
    rows = list(reader)
    if len(rows) != count or any(None in row or any(value is None for value in row.values()) for row in rows):
        raise ValueError("原生导出不完整或列数不一致")
    if marker_path.read_bytes() != before:
        raise ValueError("原生数据读取期间正在重新导出，请稍后重试")
    if len(marker) == 4:
        checksum, size = _rolling_checksum(raw)
        if marker[3] != f"{checksum}:{size}":
            raise ValueError("原生导出内容与完成标记不一致，请重新执行")
    return rows, hashlib.sha256(raw).hexdigest(), marker


def _rolling_checksum(data):
    """Same byte checksum used by native SKILL; integrity only, not authentication."""
    checksum = 0
    for byte in data:
        checksum = (checksum * 31 + byte) % 65521
    return checksum, len(data)


def read_catalogue(folder):
    rows, digest, _marker = _export_rows(folder, "catalog", "IMP_CATALOG", {"TYPE", "NAME", "VALUE", "DETAIL"})
    result = {"layers": [], "nets": [], "pairs": [], "colors": [], "rules": [], "board": ""}
    result["catalog_sha256"] = digest
    result["session_token"] = _marker[1]
    for row in rows:
        if not {"TYPE", "NAME", "VALUE", "DETAIL"}.issubset(row):
            raise ValueError("无效原生目录格式")
        kind = row["TYPE"]
        if kind == "BOARD":
            result["board"] = row["NAME"]
        elif kind in ("LAYER", "NET", "PAIR", "COLOR", "RULE"):
            key = {"LAYER": "layers", "NET": "nets", "PAIR": "pairs", "COLOR": "colors", "RULE": "rules"}[kind]
            result[key].append(row)
        else:
            raise ValueError("未知原生目录记录")
    if not result["board"] or not result["layers"]:
        raise ValueError("没有当前 BRD 的板名/铜层，不能分析")
    if sum(row["TYPE"] == "BOARD" for row in rows) != 1:
        raise ValueError("原生目录混入多份板记录")
    for key in ("layers", "nets", "pairs", "colors"):
        if len({r["NAME"] for r in result[key]}) != len(result[key]):
            raise ValueError("原生目录中层/网络/差分对/颜色名称重复")
    layer_names = {r["NAME"] for r in result["layers"]}
    if any(row["VALUE"] not in layer_names for row in result["rules"]):
        raise ValueError("约束关联到未知铜层")
    net_names = {row["NAME"] for row in result["nets"]}
    rule_keys = [(row["NAME"], row["VALUE"]) for row in result["rules"]]
    if any(name not in net_names for name, _layer in rule_keys) or len(rule_keys) != len(set(rule_keys)):
        raise ValueError("约束关联到未知 Net 或存在重复条目")
    if not result["colors"]:
        raise ValueError("未读取到 Allegro 调色板，不能进行颜色筛选")
    for color in result["colors"]:
        rgb = [int(v) for v in color["VALUE"].split(",")]
        if not 1 <= int(color["NAME"]) <= 255 or len(rgb) != 3 or any(not 0 <= v <= 255 for v in rgb):
            raise ValueError("无效 Allegro 调色板数据")
    try:
        token_after = json.loads((Path(folder) / "session.json").read_text(encoding="utf-8-sig"))["token"]
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("插件会话记录无效") from exc
    if token_after != _marker[1]:
        raise ValueError("插件会话在读取期间变化，请重新导入")
    return result


def read_results(folder):
    rows, _digest, _marker = _export_rows(folder, "results", "IMP_RESULTS",
                        {"NET", "LAYER", "WIDTH_MIL", "SPACING_MIL", "PAIR", "IMPEDANCE_RULE", "PHYSICAL_CSET",
                         "BRD", "UNITS", "MODE", "KIND", "X1_UU", "Y1_UU", "X2_UU", "Y2_UU"})
    import math
    boards = set()
    layers = set()
    modes = {"geometry", "impedance", "pair"}
    for row in rows:
        width = float(row["WIDTH_MIL"])
        if not math.isfinite(width) or width <= 0 or not row["LAYER"].startswith("ETCH/") or not row["BRD"] or not row["UNITS"]:
            raise ValueError("结果没有有效实测宽度/铜层")
        if row["MODE"] not in modes or row["KIND"] not in ("line", "arc"):
            raise ValueError("结果的筛选模式或走线类型无效")
        boards.add(row["BRD"])
        layers.add(row["LAYER"])
        if any(not math.isfinite(float(row[key])) for key in ("X1_UU", "Y1_UU", "X2_UU", "Y2_UU")):
            raise ValueError("结果坐标无效")
        if row["SPACING_MIL"]:
            spacing = float(row["SPACING_MIL"])
            if not math.isfinite(spacing) or spacing < 0:
                raise ValueError("结果间距无效")
    if len(boards) > 1 or len(layers) > 1:
        raise ValueError("原生结果混入不同 BRD 或铜层")
    if len({row["MODE"] for row in rows}) > 1:
        raise ValueError("原生结果混入不同筛选模式")
    return rows
