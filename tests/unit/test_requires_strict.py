"""B173：规则写了 `requires`，却没有任何执行点。

规格三处都写着“缺 requires 则 BLOCKED”（07 §300/§321、01 §140、18 §107），
但全仓没有代码读它。实测：只给主身份时 `idor_cross_account`（requires
`auth:secondary`）照样跑，最后落 `baseline_abnormal`——读者分不清
“目标没问题”和“我缺第二个身份”。

本轮先把**判定**和**开关**做出来，默认关：

* `unmet_requirements()` 只回答“这一轮满足不了哪些”；
* `run(..., strict_requires=True)` 才真的 BLOCKED，且**一个请求都不发**。

默认关是刻意的：一开就会让 bench 里一批多身份用例集体变 BLOCKED，
指标当场失真。默认怎么定，需要人拍板（见 H31 §2.3）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
for extra in (ROOT, ROOT / "tests"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))


class _Meta:
    def __init__(self, *requires: str) -> None:
        self.requires = tuple(requires)


class _Oracle:
    """最小 oracle 桩：只需要 meta.requires、selector_tags、params。"""

    def __init__(self, *requires: str) -> None:
        self.meta = _Meta(*requires)
        self.selector_tags = ("object_read",)
        self.params = type("P", (), {"repeat": 1})()

    def baseline_params(self, binding: object) -> dict[str, str]:  # noqa: ARG002
        return {}


class _NeverCalled:
    """任何 send 都记一笔并抛锚——用来证明“一个请求都没发”。"""

    def __init__(self) -> None:
        self.calls = 0

    def send(self, *a, **kw):  # noqa: ANN002, ANN003, ANN201
        self.calls += 1
        raise RuntimeError("transport called")


def test_unmet_requirements_maps_inputs() -> None:
    from veripen_oracles.common.harness import unmet_requirements

    o = _Oracle("auth:primary", "auth:secondary")
    assert unmet_requirements(o, token=None) == ["auth:primary", "auth:secondary"]
    assert unmet_requirements(o, token="t") == ["auth:secondary"], "主身份有了，第二身份仍然缺"


def test_unknown_requirement_is_fail_closed() -> None:
    """不认识的 requirement 按“不满足”处理：宁可少判，不可假判。"""
    from veripen_oracles.common.harness import unmet_requirements

    assert unmet_requirements(_Oracle("weird:thing"), token="t") == ["weird:thing"]


def test_oob_and_approval_are_explicit() -> None:
    from veripen_oracles.common.harness import unmet_requirements

    o = _Oracle("oob:listener", "approval:reversible_write")
    assert unmet_requirements(o) == ["oob:listener", "approval:reversible_write"]
    assert unmet_requirements(o, oob=object(), approval=object()) == []


def test_strict_mode_blocks_without_sending() -> None:
    from veripen_core.models import Verdict
    from veripen_oracles.common.harness import run

    transport = _NeverCalled()
    result = run(_Oracle("auth:secondary"), None, transport,
                 strict_requires=True, token="primary-only")
    assert result.verdict is Verdict.BLOCKED
    assert result.reason == "missing_input:auth:secondary"
    assert result.pairs == ()
    assert transport.calls == 0, "缺输入时发请求只会污染账本"


def test_default_is_not_strict() -> None:
    """默认关：缺输入时仍然按老路走（这里用“会抛”的 transport 证明它进场了）。"""
    from veripen_oracles.common.harness import run

    transport = _NeverCalled()
    with pytest.raises(RuntimeError, match="transport called"):
        run(_Oracle("auth:secondary"), None, transport, token="primary-only")
    assert transport.calls == 1
