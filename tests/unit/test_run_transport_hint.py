"""B221：抓不到页面时必须说清"连不上"，而不是让读者以为"目标没有可测的面"。"""

from __future__ import annotations

from pathlib import Path


def _run_source() -> str:
    return (Path(__file__).resolve().parents[2] / "veripen_cli" / "cmd_run.py").read_text(
        encoding="utf-8")


def test_fetch_failure_is_reported_with_reason() -> None:
    src = _run_source()
    assert "这一轮一页都没抓成" in src, "抓取全失败时没有任何说明"
    assert "fetch_failed" in src, "没把抓取失败的原因打出来"


def test_connection_hint_keeps_the_port() -> None:
    """提示命令用用户原样给的 target：`host` 只是主机名，丢了端口就指错地方。"""
    src = _run_source()
    assert 'getattr(args, "target", "")' in src, "提示又用回了主机名"


def test_hint_suggests_a_concrete_probe() -> None:
    src = _run_source()
    assert "curl -sS -o NUL" in src


# ── B277：拿到响应但解析不出，不许说成「一页都没抓成」──────────────
def test_responded_but_unparseable_has_its_own_sentence() -> None:
    """3 页全 404 时，上一行说「拿到响应 3 页」，这一行再说「一页都没抓成」就自相矛盾。"""
    src = _run_source()
    assert "拿到响应，但没有可解析的页" in src
    assert "目标在线，只是没给能测的面" in src


def test_stderr_explanations_are_flushed_after_stdout() -> None:
    """管道接走日志时 stdout 块缓冲、stderr 无缓冲：不 flush 解释会跑到被解释那行前面。"""
    src = _run_source()
    idx = src.index("拿到响应，但没有可解析的页")
    assert "sys.stdout.flush()" in src[:idx], "解释前没有 flush，CI 日志里会倒序"
