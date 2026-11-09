"""B177：压缩会把小输入**放大**——3 token 进，7 token 出。

`compress()` 永远返回"摘要形态"（带表头/字段名）。输入很小时，摘要的开销
超过节省，交出去的东西比交进去的还大——那不叫压缩。

现在的口径：压不动就**原样返回**，并如实标 `mode="passthrough"`。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for extra in (ROOT, ROOT / "tests"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))


def _fact(i: int, subject: str = "s") -> object:
    from veripen_core.blackboard import Fact

    return Fact(id=f"f-{i}", task_id="t", subject=f"{subject}{i}", predicate="p",
                value=f"v{i}", origin="observed", from_target=False,
                evidence_id=f"ev-{i}", supersedes=None, ttl_s=None, created_at=i)


def _tiny():  # noqa: ANN202
    from veripen_core.compression import compress

    return compress(facts=[_fact(0)], events=[{"kind": "http", "ok": True}],
                    capsule_ids=["c-1"], verdicts={"o": "VERIFIED"})


def _big():  # noqa: ANN202
    from veripen_core.compression import compress

    return compress(facts=[_fact(i) for i in range(25)],
                    events=[{"kind": "http", "ok": True} for _ in range(60)],
                    capsule_ids=["c-1"], verdicts={"o": "VERIFIED"})


def test_tiny_input_is_not_inflated() -> None:
    out = _tiny()
    assert out.tokens_after <= out.tokens_before, f"{out.tokens_before} -> {out.tokens_after}"
    assert out.mode == "passthrough", "压不动时要明说是原样返回"


def test_realistic_input_really_shrinks() -> None:
    out = _big()
    assert out.mode == "summarized"
    assert out.tokens_after < out.tokens_before
    assert out.reduction > 0.2, f"只省了 {out.reduction:.0%}"


def test_truncated_mode_still_works() -> None:
    """回落路径（只裁剪不合并）不受影响。"""
    from veripen_core.compression import compress

    out = compress(facts=[_fact(i) for i in range(25)],
                   events=[{"kind": "http", "ok": True} for _ in range(60)],
                   capsule_ids=["c-1"], verdicts={}, summarize=False)
    assert out.mode == "truncated"
    assert out.dropped.get("facts", 0) > 0
