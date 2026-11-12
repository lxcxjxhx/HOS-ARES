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


# ── B278：无可跑 oracle 时，三种原因各有各的下一步 ────────────────
def test_no_oracle_branch_gives_a_next_step_per_cause() -> None:
    """只说「为什么没有」不够：客户不知道该改契约、换面，还是去掉 --exclude。"""
    src = _run_source()
    for cause in ("no_tag", "no_oracle", "excluded"):
        assert cause in src, cause
    assert "下一步：{_NO_ORACLE_NEXT[cause]}" in src
    assert "--exclude" in src and "--openapi" in src


def test_each_cause_is_recorded_where_it_is_decided() -> None:
    """原因必须在**判定那一支**里记录，别在循环外靠猜（上一版就写成了难读的条件式）。"""
    src = _run_source()
    start = src.index('print("没有可跑的 oracle')
    end = src.index("账本：", start)
    block = src[start:end]
    assert block.count("causes.add(") == 3, block
    assert "hit == []" not in block, "又写回靠猜的条件式了"


# ── B299：连不上 ≠ 跑了判不了，也不该把 Windows 异常串当原因码 ────
def test_transport_failure_maps_to_error_with_a_stable_code() -> None:
    from veripen.report.render import Outcome
    from veripen_cli.cmd_run import _findings_view
    from veripen_oracles.common.harness import RunResult
    from veripen_core.models import Verdict

    class Meta:
        id = "sqli_bool_diff"
        name = "布尔差分"
        cwe = "CWE-89"
        impact = "read_only"
        severity = "high"

    result = RunResult(Verdict.BLOCKED, "transport_error:connection_refused", None, (), {})
    view = _findings_view(asset_id="a", oracle_id="sqli_bool_diff", meta=Meta(),
                          result=result, binding=None, capsule=None)
    assert view.outcome is Outcome.ERROR, view.outcome
    assert view.reason_code == "transport_error:connection_refused"


def test_transport_reason_has_a_human_sentence() -> None:
    from veripen.report.glossary import explain

    text = explain("transport_error:connection_refused")
    assert "连不上" in text and "端口" in text, text
