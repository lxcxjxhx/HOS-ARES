"""B337 · judge 纯净性自检要真的跑起来。

`harness.judge_source_violations` 早就写了（07 §6 专项检查：judge 里不许出现
`time.` / `random.` / `os.environ` / `open(` / `socket` / `requests`），
但**没有任何调用方**——于是"判定是纯函数"只靠 bench 三轮一致间接证明。
这里把它接到每个 oracle 的 judge 上。
"""

from __future__ import annotations

import inspect

import pytest

from hos_ares_oracles.common.harness import BANNED_IN_JUDGE, judge_source_violations
from typing import Any


def _judges() -> list[tuple[str, object]]:
    from hos_ares_oracles import ALL_ORACLES

    out: list[tuple[str, object]] = []
    for oracle in ALL_ORACLES:
        judge = getattr(oracle, "judge", None)
        if judge is None:
            judge = getattr(oracle, "judge_pair", None)
        if judge is not None:
            out.append((oracle.meta.id, judge))
    return out


def test_there_are_judges_to_check() -> None:
    assert _judges(), "没扫到任何 judge——检查口径失效"


@pytest.mark.parametrize(("oracle_id", "judge"), _judges(), ids=lambda v: str(v)[:24])
def test_judge_source_is_pure(oracle_id: str, judge: Any) -> None:
    source = inspect.getsource(judge)
    hits = judge_source_violations(source)
    assert not hits, f"{oracle_id} 的 judge 里出现禁用符号：{hits}"


def test_the_banned_list_is_not_empty() -> None:
    assert len(BANNED_IN_JUDGE) >= 4
