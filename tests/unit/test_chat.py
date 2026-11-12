

# ── B362：规则模式要说怎么升级 ──────────────────────────────────
def test_rules_mode_tells_how_to_enable_a_model(tmp_path, capsys) -> None:  # noqa: ANN001
    """`model: rules` 读不出是"没配"还是"就是这样"——启动行下面得给一条可执行提示。"""
    import sys

    from veripen_cli.cmd_chat import main as chat_main

    old = sys.stdin
    try:
        sys.stdin = open(__file__, encoding="utf-8")  # 任何可读流都行，读完即 EOF
        rc = chat_main(["--db", str(tmp_path / "e.db"), "--root", str(tmp_path)])
    finally:
        sys.stdin.close()
        sys.stdin = old
    out = capsys.readouterr().out
    assert rc == 0
    assert "规则模式" in out and "VERIPEN_MODEL_BASE_URL" in out, out[:400]
