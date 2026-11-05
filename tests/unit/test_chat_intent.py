"""B77 · 意图解析（H20 判据 A/B/F 的解析部分）。

规则层的价值不是"多聪明"，是**说错时能回显理解成了什么**，
让用户当场纠正，而不是静默做错事。
"""

from __future__ import annotations

from veripen_core.agent.intent import parse

SEP = chr(0x2F)


# ── A：说人话能落到工具 ────────────────────────────────────────
def test_read_phrase() -> None:
    p = parse(f"看下 out{SEP}x.txt 里写了什么")
    assert p.ok and p.steps[0].tool == "read"
    assert p.steps[0].args["path"] == f"out{SEP}x.txt"


def test_write_phrase() -> None:
    p = parse(f"往 out{SEP}y.txt 写 hello")
    assert p.ok and p.steps[0].tool == "write"
    assert p.steps[0].args["content"] == "hello"


def test_edit_phrase_keeps_old_and_new() -> None:
    """中文"改成"必须整体识别：切成"改"+"成"会把 new 变成"成"。"""
    p = parse(f"把 out{SEP}y.txt 里的 hello 改成 bye")
    assert p.ok and p.steps[0].tool == "edit"
    assert p.steps[0].args == {"path": f"out{SEP}y.txt", "old": "hello", "new": "bye"}


def test_shell_phrase() -> None:
    p = parse("执行 python -V")
    assert p.ok and p.steps[0].tool == "sh"
    assert p.steps[0].args["cmd"] == "python -V"


def test_status_phrase() -> None:
    assert parse("状态").steps[0].tool == "status"


def test_bare_path_defaults_to_read() -> None:
    p = parse(f"out{SEP}a.txt")
    assert p.ok and p.steps[0].tool == "read"


# ── B：理解错了要能看见 ────────────────────────────────────────
def test_understood_as_is_always_filled() -> None:
    for text in ("看下 x.txt", "状态", "执行 echo hi", "完全不相关的一句话"):
        p = parse(text)
        assert p.understood_as, f"{text!r} 没有回显理解结果"


def test_unknown_phrase_lists_examples() -> None:
    p = parse("把这件事办一下")
    assert not p.ok
    assert "没听懂" in p.understood_as and "看下" in p.understood_as


# ── F：指代沿用上一轮的路径 ────────────────────────────────────
def test_pronoun_reuses_previous_path() -> None:
    history = [f"看下 out{SEP}a.txt"]
    p = parse("再读一遍它", history=history)
    assert p.ok and p.steps[0].args["path"] == f"out{SEP}a.txt"


def test_no_history_no_pronoun_guess() -> None:
    p = parse("读一下它")
    assert not p.ok, "没有历史时不许凭空猜路径"
