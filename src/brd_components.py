"""从 .brd 归档的 `objects/Part.xml` 读器件清单 (数量 + 位号)。

Allegro 把每颗器件的位号写在 Part.xml 的 `<o N="R7"/>` 节点里；`Gate.xml` /
`PartDefn.xml` 里是封装、料号等（本模块不展开）。只有**真实 PCB 设计**才带
Part.xml —— FAB 拼板图 / 纯板框图纸根本没有这个对象，这时如实返回 ok=False，
绝不拿丝印图形去猜"有几个器件"。

判定口径：
    器件数 = Part.xml 里位号节点的个数（去重后）
    位号   = 去重后的位号字符串，按"前缀 + 数字"自然排序
    分类   = 按位号前缀归到 电阻/电容/IC芯片/连接器… （前缀表见 _PREFIX_CATEGORY）
"""
import re
from collections import Counter, OrderedDict
from typing import Dict, List

from xml.etree import ElementTree as ET

# 位号前缀 -> 中文类别 (没列到的前缀原样显示)
_PREFIX_CATEGORY = {
    "R": "电阻", "RN": "电阻排", "RP": "电阻排", "VR": "可调电阻",
    "C": "电容", "CP": "钽电容",
    "L": "电感", "FB": "磁珠", "F": "保险丝", "T": "变压器",
    "U": "IC芯片", "IC": "IC芯片", "Q": "三极管/MOS管",
    "D": "二极管", "LED": "发光二极管",
    "X": "晶振", "Y": "晶振", "OSC": "晶振",
    "J": "连接器/接插件", "P": "连接器/接插件",
    "CN": "连接器/接插件", "CON": "连接器/接插件",
    "SW": "开关", "K": "继电器", "S": "开关",
    "TP": "测试点", "MH": "安装孔", "H": "安装孔", "FH": "安装孔",
    "B": "电池", "BT": "电池", "BZ": "蜂鸣器", "M": "电机", "MOV": "压敏电阻",
}


def _prefix_of(refdes: str) -> str:
    """R3438 -> R；FB44 -> FB；U93 -> U (只取数字前面那段字母)。"""
    m = re.match(r"^([A-Za-z]+)\d", refdes or "")
    return m.group(1).upper() if m else ""


def _natural_key(refdes: str):
    parts = re.split(r"(\d+)", refdes or "")
    key = []
    for part in parts:
        if part.isdigit():
            key.append((1, int(part), ""))
        elif part:
            key.append((0, 0, part.upper()))
    return key


def extract_components(objects: Dict[str, bytes]) -> dict:
    """从归档对象里读器件清单；没有 Part.xml 时如实说明原因。"""
    data = objects.get("objects/Part.xml")
    if not data:
        return {
            "ok": False, "count": 0, "refdes": [], "by_prefix": {},
            "by_category": {}, "reason": "BRD 归档里没有 objects/Part.xml (FAB 拼板图/纯板框图纸常见)",
        }
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        return {
            "ok": False, "count": 0, "refdes": [], "by_prefix": {},
            "by_category": {}, "reason": f"objects/Part.xml 解析失败: {exc}",
        }

    refdes: List[str] = []
    seen = set()
    for node in root.findall("o"):
        name = (node.get("N") or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        refdes.append(name)
    refdes.sort(key=_natural_key)

    prefixes = Counter(_prefix_of(r) for r in refdes)
    categories: "OrderedDict[str, int]" = OrderedDict()
    for prefix, count in sorted(prefixes.items(), key=lambda kv: (-kv[1], kv[0])):
        label = _PREFIX_CATEGORY.get(prefix, prefix or "未知前缀")
        categories[label] = categories.get(label, 0) + count

    return {
        "ok": True,
        "count": len(refdes),
        "refdes": refdes,
        "by_prefix": {p: c for p, c in sorted(prefixes.items(), key=lambda kv: (-kv[1], kv[0]))},
        "by_category": dict(categories),
        "source": "objects/Part.xml 位号清单",
        "confidence": "high",
    }


def component_summary_line(comp: dict, limit: int = 6) -> str:
    """一行摘要: 器件 245 个 (电阻 120 / 电容 90 / IC 35 …), 用于 GUI 与报告。"""
    if not comp or not comp.get("ok"):
        return comp.get("reason", "BRD 无器件数据") if comp else "未读取器件信息"
    parts = [f"{label} {count}" for label, count in list((comp.get("by_category") or {}).items())[:limit]]
    rest = len(comp.get("by_category") or {}) - limit
    if rest > 0:
        parts.append(f"其它 {rest} 类")
    return f"器件 {comp['count']} 个 (" + " / ".join(parts) + ")"
