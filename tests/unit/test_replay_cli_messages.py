"""B222：`replay` 的两条提示不能互相指——两个出口都说"用另一个"，等于都走不通。"""

from __future__ import annotations

from pathlib import Path

SRC = (Path(__file__).resolve().parents[2] / "veripen_cli" / "cmd_replay.py"
       ).read_text(encoding="utf-8")


def test_selftest_message_says_what_it_needs() -> None:
    """`--selftest` 的本质是篡改真证据，所以它必然要一份 capsule——说清。"""
    assert "--selftest 需要一份 capsule" in SRC
    assert "--selftest 需要 --capsule\")" not in SRC, "又回到那句自指的话"


def test_no_argument_message_does_not_offer_a_dead_end() -> None:
    assert "（或用 --selftest）" not in SRC, "无参提示又把 --selftest 说成替代品"
    assert "--capsule <path> --selftest" in SRC
