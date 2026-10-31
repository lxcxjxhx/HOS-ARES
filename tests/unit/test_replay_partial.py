"""B4 ``PARTIAL`` 的精确边界（06 §5.1.2 / §8 断言 9）。

PARTIAL 的前提是**结论仍成立**。对照没跑成 = 结论未经检验 → 必须是 FAILED。
"""

from __future__ import annotations

import hashlib

import capsule_fixture as F
import pytest

from veripen_core.evidence import ReplayEnv, ReplayMode, TraceResult, replay
from veripen_core.models import ReplayStatus


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class Scripted:
    def __init__(self, by_role: dict[str, TraceResult]) -> None:
        self.by_role = by_role

    def send(self, step, binding):  # type: ignore[no-untyped-def]
        return self.by_role[step.role]


def _traces() -> dict[str, TraceResult]:
    cap = F.capsule()
    return {
        s.role: TraceResult(status=s.response.status, body_sha256=s.response.body_sha256)
        for s in cap.steps
    }


def _run(by_role: dict[str, TraceResult]):
    env = ReplayEnv(now=F.NOW, mode=ReplayMode.LIVE, routeset=F.routeset())
    return replay(F.sealed(), env, Scripted(by_role))


# ── 是 PARTIAL 的三条 ──────────────────────────────────────────────
def test_partial_body_changed() -> None:
    t = _traces()
    t["probe"] = TraceResult(status=200, body_sha256=_sha("changed"))
    assert _run(t).state is ReplayStatus.PARTIAL


def test_partial_status_200_to_201() -> None:
    t = _traces()
    t["probe"] = TraceResult(status=201, body_sha256=_sha("same-json-different-bytes"))
    result = _run(t)
    assert result.state is ReplayStatus.PARTIAL


def test_partial_when_one_step_net_failed_but_conclusion_holds() -> None:
    """baseline 网络失败、probe/control 成功：结论仍成立 → PARTIAL + network_partial。"""
    t = _traces()
    t["baseline"] = TraceResult(status=0, body_sha256="", error="Timeout")
    result = _run(t)
    assert result.state is ReplayStatus.PARTIAL
    assert result.reason == "network_partial"


# ── 不是 PARTIAL 的三条（都必须是 FAILED）──────────────────────────
def test_control_network_failure_is_not_partial() -> None:
    t = _traces()
    t["control"] = TraceResult(status=0, body_sha256="", error="Timeout")
    result = _run(t)
    assert result.state is ReplayStatus.FAILED
    assert result.reason == "control_missing"


def test_control_blocked_by_permission_is_not_partial() -> None:
    """403 长一样，但"对照跑不成"与"对照成立"必须可区分（29 零件 B）。"""
    t = _traces()
    t["control"] = TraceResult(
        status=403, body_sha256=_sha("denied by waf"), return_code_semantics="denied_request"
    )
    result = _run(t)
    assert result.state is ReplayStatus.FAILED
    assert result.reason == "control_missing"


def test_control_semantics_ok_counts_as_denied() -> None:
    """对照组本身 403 且语义 ok = 对照成立（这是成功路径，不是失败）。"""
    t = _traces()
    t["control"] = TraceResult(status=403, body_sha256=_sha("forbidden"), return_code_semantics="ok")
    assert _run(t).state is ReplayStatus.PARTIAL  # body 变了但三条件成立


def test_only_partial_steps_testable_is_failed() -> None:
    """probe 失败、对照成立：三条件不成立 → FAILED（不是 PARTIAL）。"""
    t = _traces()
    t["probe"] = TraceResult(status=0, body_sha256="", error="ConnectionReset")
    assert _run(t).state is ReplayStatus.FAILED


def test_three_conditions_broken_never_partial() -> None:
    t = _traces()
    t["control"] = TraceResult(status=200, body_sha256=_sha("leak"))
    t["probe"] = TraceResult(status=200, body_sha256=_sha("changed"))
    assert _run(t).state is ReplayStatus.FAILED


# ── 未被声明的指纹不得冒充成立 ────────────────────────────────────
def test_undeclared_fingerprint_key_not_checked_as_conclusion() -> None:
    """``probe_body_eq_baseline`` 是细节键，body 变了不该把结论判死。"""
    t = _traces()
    t["probe"] = TraceResult(status=200, body_sha256=_sha("changed"))
    result = _run(t)
    assert result.fingerprint["probe_body_eq_baseline"] is False
    assert result.state is ReplayStatus.PARTIAL
