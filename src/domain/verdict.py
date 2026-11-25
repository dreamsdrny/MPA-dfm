"""统一判定口径：一份分析只有一个权威结论。

优先级固定为 不通过(fail) > 需复核(review) > 通过(pass)：
  - 只要有不符合项就是「不通过」，缺数据再多也不改变这一结论；
  - 没有不符合项但存在缺数据/部分覆盖/复核候选时是「需复核」，绝不放行；
  - 只有全部放行条件满足才是「通过」。

verdict_reasons 逐条列出结论依据（缺什么、哪组候选、覆盖缺口），
供界面与报告展示，保证结论可追溯；缺失数据永远不会被静默当作通过。
"""

VERDICT_LABELS = {"fail": "不通过", "review": "需复核", "pass": "通过"}


def finalize_verdict(result, geometry=None):
    """就地重算 error/warning 计数并写入唯一结论，返回 result。

    geometry 为 analyze_gerbers 的 bundle；传入时把几何缺数据/候选/
    部分覆盖纳入放行门槛。不传时仅按参数判定（兼容独立参数分析）。
    """
    items = result.get("items", [])
    error_count = sum(it.get("status") == "fail" for it in items)
    warning_count = sum(it.get("status") == "warn_limit" for it in items)
    result["error_count"] = error_count
    result["warning_count"] = warning_count
    result["passed"] = error_count == 0

    reasons = []
    if error_count:
        reasons.append(f"存在 {error_count} 项不符合制程能力")
    reasons.extend(result.get("release_blockers") or [])
    if result.get("position_review_count"):
        reasons.append(f"丝印位置存在 {result['position_review_count']} 处需复核候选，不能直接放行")
    if geometry is not None:
        for error in result.get("gerber_errors", []):
            reasons.append(f"Gerber 检查缺失/不一致：{error}")
        if result.get("geometry_review_count"):
            reasons.append(f"制造几何存在 {result['geometry_review_count']} 组需复核候选"
                           "（铜/阻焊/钻孔/铜平衡），非缺陷证明")
        if geometry.get("geometry_complete") is not True:
            reasons.append("制造几何为部分覆盖：缺完整拓扑/网络级 DRC，不能据此放行")

    if error_count:
        code = "fail"
    elif reasons:
        code = "review"
    else:
        code = "pass"
    result["verdict_code"] = code
    result["verdict_reasons"] = reasons
    result["releasable"] = code == "pass"
    if code == "fail":
        result["verdict"] = "不通过（存在不符合项）"
    elif code == "review":
        result["verdict"] = "需复核（未满足放行条件）"
    else:
        result["verdict"] = "通过（有风险项）" if warning_count else "通过"
    if result.get("risk"):
        result["risk"]["verdict"] = result["verdict"]
    return result
