"""SMT panel applicability rules; object geometry is supplied, never read here."""


def _apply_panel_review(result, facts):
    if str(result.get("meta", {}).get("factory", "")).strip().lower() not in ("立景", "lijing"):
        return
    long_side = facts.get(("design", "panel_size_x"), {}).get("value")
    short_side = facts.get(("design", "panel_size_y"), {}).get("value")
    thickness = facts.get(("design", "board_thickness"), {}).get("value")
    if long_side is None or short_side is None:
        return
    if (long_side >= 285 or short_side >= 237.5) and (thickness is None or thickness < 1.6):
        result.setdefault("object_findings", []).append(dict(id="LJ-PANEL-THICKNESS", label="拼板上限与板厚", status="review",
            reason="拼板尺寸达到上限的 95%，板厚未确认或小于 1.6mm，需供应商复核", source="立景能力采集表", severity="medium", unit="mm"))
        for item in result["items"]:
            if item["field"] in ("panel_size_x", "panel_size_y") and item["status"] == "pass":
                item.update(status="review", reason="拼板尺寸达到上限的 95%，板厚未确认或小于 1.6mm，需供应商复核")
