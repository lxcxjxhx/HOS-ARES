

# ── B328：平台报告要写黑名单（终端打了、报告没写）──────────────────
def test_platform_report_states_the_deny_list() -> None:
    import inspect

    from veripen_cli import cmd_platform

    src = inspect.getsource(cmd_platform.main)
    assert '"- 黑名单：' in src, "报告头要写黑名单"
    assert "命中的资产整面 BLOCKED（不发包）" in src
