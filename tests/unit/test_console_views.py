

# ── B400：控制台与 hunt.json 也要看到能力边界 ────────────────────
def test_console_coverage_carries_capability_limits() -> None:
    """运维在控制台看的是矩阵；矩阵全绿不等于"都测过了"。"""
    import web.console as console

    view = console.coverage_from_findings([], capability_limits=console._capability_limits())
    checks = {item["check"] for item in view["capability_limits"]}
    # S3 在 B410 已实现（不再属于边界），边界里现在至少还有这些
    assert {"M7", "csp_absent", "cors_misconfig"} <= checks, checks
    # 没有边界时不留空字段（空就是空 vs 没有这条信息）
    assert "capability_limits" not in console.coverage_from_findings([])
