"""执行调度: 按选择跑能力, 汇总成一份 bundle。

bundle 是"一次分析的全部结果", 报告生成器 (writers) 只认 bundle。
一项能力抛异常不会中断整轮分析 —— 那项记成 fail 并写明异常, 其余照跑
(现场用的时候, 一块板子跑到第 30 项崩掉, 前面的结果必须还在)。
"""
import os
import traceback
from datetime import datetime
from typing import Dict, Iterable, List, Optional

from src.brd_analysis.catalog import CATALOG
from src.brd_analysis.context import Context
from src.brd_analysis.model import (CATEGORY_LABELS, CATEGORY_ORDER, FAIL, INFO,
                                    STATUS_LABELS, Result)


def _select(only: Optional[Iterable[str]] = None,
            categories: Optional[Iterable[str]] = None,
            skip: Optional[Iterable[str]] = None) -> List:
    caps = CATALOG.all
    if categories:
        want = {c.strip() for c in categories if c and c.strip()}
        caps = [c for c in caps if c.category in want]
    if only:
        want = {c.strip() for c in only if c and c.strip()}
        caps = [c for c in caps if c.id in want or c.category in want]
    if skip:
        drop = {c.strip() for c in skip if c and c.strip()}
        caps = [c for c in caps if c.id not in drop]
    return caps


def run(ctx: Context, only: Optional[Iterable[str]] = None,
        categories: Optional[Iterable[str]] = None,
        skip: Optional[Iterable[str]] = None,
        log=None) -> dict:
    """跑一轮分析, 返回 bundle。"""
    caps = _select(only, categories, skip)
    results: List[dict] = []
    for cap in caps:
        if log:
            log(f"  [{cap.category}] {cap.name_cn} ({cap.id}) ...", "info")
        try:
            res = cap.func(ctx)
            if res is None:
                res = Result(cap, INFO, "该能力未返回结果")
            if not isinstance(res, Result):
                raise TypeError(f"处理函数返回了 {type(res).__name__}, 期望 Result")
        except Exception as exc:  # noqa: BLE001  单项失败不能中断整轮
            res = Result(cap, FAIL, f"运行失败: {exc}")
            res.warn(traceback.format_exc().strip().splitlines()[-1])
        results.append(res.to_dict())
        if log:
            tag = {"pass": "success", "fail": "error", "warn": "warn"}.get(res.status, "info")
            log(f"      -> {STATUS_LABELS.get(res.status, res.status)}: {res.summary}", tag)

    stats = {key: 0 for key in STATUS_LABELS}
    for res in results:
        stats[res["status"]] = stats.get(res["status"], 0) + 1
    stats["total"] = len(results)
    stats["measured"] = stats.get("pass", 0) + stats.get("fail", 0) + stats.get("warn", 0)
    stats["unavailable"] = stats.get("unavailable", 0)

    by_category: Dict[str, List[str]] = {key: [] for key in CATEGORY_ORDER}
    for res in results:
        by_category.setdefault(res["category"], []).append(res["id"])

    artifacts: List[dict] = []
    for res in results:
        for art in res["artifacts"]:
            artifacts.append({
                "capability": res["id"], "capability_cn": res["name_cn"],
                "kind": art.get("kind", ""), "path": art.get("path", ""),
                "note": art.get("note", ""),
            })

    bundle = {
        "ok": True,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "board": ctx.summary(),
        "results": results,
        "by_category": by_category,
        "categories": [{"key": k, "label": CATEGORY_LABELS[k],
                        "count": len(by_category.get(k) or [])} for k in CATEGORY_ORDER],
        "stats": stats,
        "artifacts": artifacts,
        "catalog_errors": CATALOG.errors,
        "output_dir": ctx.out_dir,
    }
    if log:
        log(f"  共 {stats['total']} 项: 通过 {stats['pass']}, 不符合 {stats['fail']}, "
            f"需关注 {stats['warn']}, 信息 {stats['info']}, "
            f"数据不足 {stats['unavailable']}", "success")
    return bundle


def capability_rows() -> List[dict]:
    """能力目录 (给 CLI/界面列出"这个工具能做什么")。"""
    rows = []
    for cap in CATALOG.all:
        rows.append({
            "id": cap.id, "category": cap.category,
            "category_label": CATEGORY_LABELS.get(cap.category, cap.category),
            "name": cap.name, "name_cn": cap.name_cn, "desc": cap.desc,
            "engine": cap.engine, "engine_label": cap.engine_label,
            "needs": cap.needs, "outputs": cap.outputs,
        })
    return rows


def run_all(brd_path: str, out_dir: str = "", export_files: Optional[Iterable[str]] = None,
            only: Optional[Iterable[str]] = None,
            categories: Optional[Iterable[str]] = None,
            skip: Optional[Iterable[str]] = None, log=None) -> dict:
    """一站式入口: 建上下文 -> 跑能力 -> 返回 bundle。"""
    ctx = Context(brd_path, out_dir=out_dir, export_files=export_files,
                  extra_dirs=[out_dir] if out_dir else [])
    if not os.path.exists(brd_path or ""):
        bundle = {"ok": False, "error": f"文件不存在: {brd_path}", "results": [],
                  "stats": {}, "board": {}, "artifacts": []}
        return bundle
    return run(ctx, only=only, categories=categories, skip=skip, log=log)
