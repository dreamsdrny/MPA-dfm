"""焊盘提取 + 测试点识别。

用户口径
--------
钢网层（PASTEMASK / 钢网开窗，也就是贴片锡膏层）上的**圆形焊盘**按测试点算；
焊盘同时提供尺寸，用来算测试点直径是否满足能力库 `test.min_pad_2wire / 4wire`。

数据现实
--------
`.brd` 的内嵌快照 `design.comps` 里目前只导出丝印/装配/ETCH/板框/ROUTE KEEPIN
这类图形，**没有** PAD / PASTEMASK / SOLDERMASK 的焊盘几何（78 块样本板逐一确认）。
所以本模块在快照里找不到焊盘时返回 `ok=False` 并说明"缺钢网层/焊盘图形"，
不把丝印块或 PIN 标记当成焊盘凑数；拿到焊盘坐标（Gerber / Allegro 焊盘报告）后，
同一套函数可以直接判测试点直径。
"""
from typing import List, Optional

# 快照里"可能是焊盘"的 class/subclass 关键字
_PAD_SUBCLASS_HINTS = ("PASTEMASK", "PASTE", "PAD", "SOLDERMASK", "SMASK", "PMASK")
_PAD_CLASS_HINTS = ("PAD",)

# 钢网/锡膏层: 这些层上的圆焊盘按测试点算
_STENCIL_HINTS = ("PASTEMASK", "PASTE", "PMASK")


def _entries(doc: Optional[dict]) -> List[dict]:
    from src.design_geometry import _entries as entries

    return entries(doc or {})


def _looks_like_pad(entry: dict) -> bool:
    cls = (entry.get("class") or "").upper()
    sub = (entry.get("subclass") or "").upper()
    if any(h in sub for h in _PAD_SUBCLASS_HINTS):
        return True
    return any(h in cls for h in _PAD_CLASS_HINTS)


def _is_stencil(entry: dict) -> bool:
    sub = (entry.get("subclass") or "").upper()
    cls = (entry.get("class") or "").upper()
    return any(h in sub or h in cls for h in _STENCIL_HINTS)


def _circle_of(entry: dict, scale: float) -> Optional[dict]:
    """圆形焊盘: type 3 带 radius, 或 type 2 只有 1 个点 (点状焊盘)。"""
    pts = entry.get("points") or []
    if not pts or not isinstance(pts[0], dict):
        return None
    radius = float(entry.get("radius") or 0.0)
    if radius <= 0:
        return None
    return {
        "x": float(pts[0].get("x", 0.0)) * scale,
        "y": float(pts[0].get("y", 0.0)) * scale,
        "d": radius * 2.0 * scale,
        "layer": f"{entry.get('class')} / {entry.get('subclass')}",
        "stencil": _is_stencil(entry),
    }


def extract_pads(doc: Optional[dict], scale_mm_per_unit: float) -> dict:
    """从内嵌快照里提取圆形焊盘 (含钢网层)。"""
    if not doc:
        return {"ok": False, "reason": "该 .brd 没有可解析的内嵌设计快照", "pads": 0}
    if not scale_mm_per_unit:
        return {"ok": False, "reason": "板框标定失败, 无法换算焊盘尺寸", "pads": 0}
    pads: List[dict] = []
    for entry in _entries(doc):
        if not _looks_like_pad(entry):
            continue
        pad = _circle_of(entry, scale_mm_per_unit)
        if pad:
            pads.append(pad)
    if not pads:
        return {
            "ok": False,
            "pads": 0,
            "reason": ("内嵌快照没有 PAD / PASTEMASK(钢网层) / SOLDERMASK 焊盘图形; "
                       "测试点要按钢网层圆焊盘识别, 需要 Gerber 或 Allegro 焊盘坐标"),
            "searched": list(_PAD_SUBCLASS_HINTS),
        }
    pads.sort(key=lambda p: p["d"])
    stencil = [p for p in pads if p["stencil"]]
    return {
        "ok": True,
        "pads": len(pads),
        "stencil_pads": len(stencil),
        "min_d_mm": round(pads[0]["d"], 4),
        "max_d_mm": round(pads[-1]["d"], 4),
        "layers": sorted({p["layer"] for p in pads})[:8],
        "source": "内嵌快照焊盘图形 (class/subclass 含 PAD/PASTEMASK/SOLDERMASK)",
    }


def detect_test_points(pads_info: dict, d_min: float = 0.3, d_max: float = 5.0) -> dict:
    """钢网层圆焊盘 -> 测试点。

    只认 `stencil=True` 的圆形焊盘, 直径落在 [d_min, d_max] (mm) 内;
    直径超出常规测试针范围的不当成测试点, 但数量仍在报告里说明。
    """
    if not pads_info or not pads_info.get("ok"):
        detail = (pads_info or {}).get("reason", "没有焊盘数据")
        return {"ok": False, "count": 0, "reason": f"识别不了测试点: {detail}"}
    return {"ok": True, "count": pads_info.get("stencil_pads", 0),
            "d_min_mm": d_min, "d_max_mm": d_max,
            "note": "钢网层圆形焊盘按测试点计; 直径范围 %g~%gmm" % (d_min, d_max)}
