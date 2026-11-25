"""Normalize legacy engine summaries without modifying their rule decisions."""
from src.domain.models import AnalysisSummary, Confidence, Coverage, Evidence, InputSource, release_verdict


def summarize(service, result, fingerprints=()):
    reasons = list(result.get("verdict_reasons") or result.get("release_blockers") or [])
    if service == "checklist":
        stats = result.get("stats") or {}
        total = stats.get("total", 0)
        evaluated = stats.get("auto_pass", 0) + stats.get("auto_fail", 0) + stats.get("na", 0)
        failed = bool(stats.get("auto_fail"))
        review = bool(stats.get("manual") or stats.get("auto_verify") or not total)
        # A filled checklist is not a geometric/manufacturing release proof.
        complete = False
        reasons.append("Checklist 自动填写不替代人工签核及制造几何检查")
    elif service == "brd":
        rows = result.get("results") or []
        total = len(rows)
        evaluated = sum(row.get("status") in ("pass", "fail", "warn") for row in rows)
        failed = any(row.get("status") == "fail" for row in rows)
        review = any(row.get("status") != "pass" for row in rows) or not result.get("ok")
        complete = False
        reasons.append("BRD 多能力分析为所选能力覆盖，不等同完整 DRC/制造放行")
    elif service == "manufacturing":
        rows = (result.get("reconciliation") or {}).get("rows") or []
        total = len(rows)
        evaluated = sum(row.get("status") != "unverified" for row in rows)
        failed = any(row.get("status") in ("missing_in_bom", "missing_in_layout") for row in rows)
        review = result.get("status") != "verified" or bool(result.get("warnings"))
        complete = False
        reasons.append("制造资料/BOM 关联不替代完整制造 DFM")
    else:
        rows = result.get("items") or []
        total = len(rows)
        evaluated = sum(row.get("status") in ("pass", "fail", "warn_limit", "not_applicable") for row in rows)
        failed = (any(row.get("status") == "fail" for row in rows)
                  or result.get("verdict_code") == "fail" or bool(result.get("error_count"))
                  or bool((result.get("counts") or {}).get("fail")))
        if service == "pcb":
            review = result.get("verdict_code") == "review"
            complete = bool(result.get("releasable") and result.get("geometry_complete"))
        else:
            review = bool(result.get("manual_review_count") or result.get("object_errors")
                          or (result.get("counts") or {}).get("review"))
            complete = bool(total and evaluated == total and not review and result.get("coverage", 0) == 1
                            and result.get("geometry_complete") is True)
    missing = tuple(str(row.get("field") or row.get("id") or row.get("item") or "缺失项")
                    for row in (result.get("items") or [])
                    if row.get("status") not in ("pass", "fail", "warn_limit", "not_applicable"))
    missing += tuple((result.get("coverage") or {}).get("missing_items", [])) if isinstance(result.get("coverage"), dict) else ()
    missing += tuple(str(row.get("rule") or "几何缺失项") for row in result.get("geometry_coverage", [])
                     if row.get("status") != "measured")
    missing = tuple(dict.fromkeys(missing))
    # Counts from older engines can overlap; never turn that into complete coverage.
    evaluated = min(total, evaluated)
    coverage = Coverage(total, evaluated, missing, bool(complete and total and evaluated == total and not missing))
    if not coverage.complete:
        reasons.append("当前分析存在缺失数据或部分覆盖，仅供复核")
    sources = tuple(InputSource(path=s["path"], kind=s.get("type", "file"),
                                sha256=s.get("sha256", "")) for s in fingerprints)
    verdict = release_verdict(failed=failed, review=review, coverage=coverage)
    evidence = []
    provenance = result.get("sources") if isinstance(result.get("sources"), dict) else {}
    for row in result.get("items") or []:
        field = str(row.get("field") or row.get("id") or row.get("item") or "")
        meta = provenance.get(field) or {}
        origin = row.get("source_detail") or row.get("source") or meta.get("source") or "未提供"
        # A matching threshold is not proof of confirmed object geometry.
        raw_confidence = str(meta.get("confidence") or "unknown").lower()
        confidence = {"confirmed": Confidence.CONFIRMED, "高": Confidence.CONFIRMED,
                      "high": Confidence.CONFIRMED, "candidate": Confidence.CANDIDATE,
                      "中": Confidence.CANDIDATE, "medium": Confidence.CANDIDATE}.get(raw_confidence, Confidence.UNKNOWN)
        source = InputSource(str(origin), "evidence", str(result.get("rule_revision") or ""))
        evidence.append(Evidence(field, row.get("actual", row.get("design")), source,
                                 str(row.get("unit") or ""), confidence))
    return AnalysisSummary(service, verdict, coverage, tuple(dict.fromkeys(reasons)), sources,
                           evidence=tuple(evidence))
