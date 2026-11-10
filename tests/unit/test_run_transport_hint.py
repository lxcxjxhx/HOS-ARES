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
