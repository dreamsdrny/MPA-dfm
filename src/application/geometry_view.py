"""查看器资料装载：只读还原真实图形，不做制程判定，也不把显示简化说成实测。"""
from copy import deepcopy
from pathlib import Path

from src.domain.geometry_view import build_view_model
from src.domain.geometry_view import pan_viewport, viewport, zoom_viewport
from src.infrastructure.snapshots import capture_sources, verify_sources


def render_view(model, area=None, size=(1100, 760), layers=None, show_issues=True):
    """查看器位图渲染出口：UI 只调应用服务，不直接依赖适配器。"""
    from src.adapters.geometry_raster import render_png
    return render_png(model, area, size, layers=layers, show_issues=show_issues)


__all__ = ["gerber_view", "odb_view", "pan_viewport", "render_view", "result_view", "viewport", "zoom_viewport"]


def _issue_rows(result):
    rows = []
    for row in (result or {}).get("problem_locations", []) or (result or {}).get("issues", []):
        rows.append(dict(x_mm=row.get("x_mm"), y_mm=row.get("y_mm"), item=row.get("item") or row.get("rule"),
                         layer=row.get("layer") or row.get("side"), status=row.get("status"),
                         measured=row.get("measured") or row.get("design"), required=row.get("required") or row.get("capability"),
                         unit=row.get("unit", "mm")))
    return rows


def gerber_view(source_path, issues=()):
    """从 Gerber 文件 / 整套目录 / 压缩包读取可绘制图层；原文件只读。"""
    from src.adapters.gerber_analysis import read_film
    from src.adapters.gerber_export import gerber_files
    from src.adapters.gerber_input import staged_gerbers

    if not str(source_path or "").strip():
        raise ValueError("请选择 Gerber 文件、目录或压缩包")
    sources = capture_sources([source_path])
    layers, warnings = [], []
    with staged_gerbers(source_path) as folder:
        files = list(gerber_files(folder))
        if not files:
            raise ValueError("目录/压缩包内没有可识别的 Gerber 文件")
        for path in files:
            try:
                film = read_film(path)
            except Exception as exc:  # noqa: BLE001  单个坏文件不能伪装成整板图形
                warnings.append(f"{path.name}: {exc}")
                continue
            if film["role"] == "unknown" or film["geometry"].is_empty:
                warnings.append(f"{path.name}: 图层角色未识别或图形为空，未绘制")
                continue
            layers.append(dict(name=path.name, role=film["role"], side=film.get("side", ""),
                               geometry=film["geometry"], objects=len(film.get("objects", [])),
                               path=film["path"]))
    verify_sources(sources)
    if not layers:
        raise ValueError("没有可绘制的 Gerber 图形：" + "；".join(warnings[:3]))
    model = build_view_model(layers, issues, source=sources[0]["path"], warnings=warnings,
                             notes=["独立读取底片图形，仅用于查看；制程判定仍以分析结果为准"])
    return dict(model=model, source=sources[0]["path"], fingerprints=sources, warnings=warnings)


def odb_view(path, step=None, issues=(), aligned=None):
    """读取 ODB++ 图层图形；未确认同版同原点时明确标为仅查看。"""
    from shapely.wkb import loads as wkb_loads

    from src.adapters.odb_reader import parse_odb

    if not str(path or "").strip():
        raise ValueError("请选择 ODB++ 归档或 Job 目录")
    sources = capture_sources([path])
    data = parse_odb(path, step=step)
    if data.get("status") == "needs_step":
        raise ValueError("ODB++ 有多个 step，请先明确单板/拼板 step：" + "、".join(data.get("steps", [])))
    layers, warnings = [], list(data.get("warnings", []))
    for film in data.get("films", []):
        try:
            geometry = wkb_loads(film["geometry_wkb"], hex=True)
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"{film['name']}: 图形解析失败 {exc}")
            continue
        if geometry.is_empty:
            continue
        layers.append(dict(name=film["name"], role="odb/" + str(film.get("role") or "unknown"),
                           side=film.get("side", ""), geometry=geometry,
                           objects=film.get("features", len(film.get("records", []) or [])),
                           path=film.get("source", "")))
    if not layers:
        raise ValueError("ODB++ 未读到可绘制图层；负片/未支持符号不会猜测图形")
    notes = ["ODB++ 制造层图形，仅用于查看；不替代 BRD 设计资料"]
    if aligned is True:
        notes.insert(0, "已通过 BRD 身份/原点对齐核对")
    elif aligned is False:
        notes.insert(0, "未确认与 BRD 同版同原点：可查看，不得据此判位置")
    model = build_view_model(layers, issues, source=sources[0]["path"], warnings=warnings, notes=notes)
    model["steps"] = list(data.get("steps", []))
    model["selected_step"] = data.get("selected_step", "")
    return dict(model=model, source=sources[0]["path"], fingerprints=sources, warnings=warnings,
                step=data.get("selected_step", ""), steps=model["steps"])


def result_view(result, kind="gerber"):
    """分析完成后按同一批资料重建查看器图形，保证看到的是本轮判定用的文件。"""
    result = deepcopy(result or {})
    issues = _issue_rows(result)
    if kind == "odb":
        objects = result.get("odb_object_result") or {}
        source = (objects.get("input") or {}).get("path", "")
        if not source:
            raise ValueError("本轮没有 ODB++ 对象资料")
        return odb_view(source, issues=issues, aligned=result.get("odb_alignment_confirmed"))
    source = result.get("gerber_folder") or result.get("input_gerber") or ""
    if not source or not Path(source).exists():
        raise ValueError("本轮没有可读回的 Gerber 目录；请先执行 PCB 分析或导入 Gerber")
    return gerber_view(source, issues=issues)
