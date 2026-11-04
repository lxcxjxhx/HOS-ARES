"""B52 上下文压缩（`H03 §4` / `H07` B52）。

判据：压缩前后判定指纹不变 · token 降 ≥30% · 摘要可回溯（merged_from）· 回落裁剪同样不改判定 ·
证据只在被要求时展开。
"""

from __future__ import annotations

import pytest

from veripen_core.blackboard import Fact
from veripen_core.compression import (
    STEP_LINES,
    compress,
    estimate_tokens,
    expand,
    summarize_facts,
    summarize_steps,
)


def _fact(seq: int, *, subject: str = "web-01", predicate: str = "server",
          value: str = "nginx", origin: str = "observed") -> Fact:
    return Fact(
        id=f"f-{seq:03d}", task_id="t1", subject=subject, predicate=predicate,
        value=value, origin=origin, from_target=False, evidence_id=f"ev-{seq}",
        supersedes=None, ttl_s=None, created_at=1_700_000_000 + seq,
    )


def _facts(n: int = 20) -> list[Fact]:
    return [_fact(i, value=f"nginx-{i}") for i in range(n)]


def _events(n: int = 20) -> list[dict[str, object]]:
    return [{"kind": "http", "tool": f"tool{i}", "ok": i % 3 != 0} for i in range(n)]


VERDICTS = {"idor_cross_account": "VERIFIED", "ssrf_oob": "BLOCKED"}


# ── A 合并与可回溯 ─────────────────────────────────────────────
def test_merge_by_subject_predicate_is_traceable() -> None:
    facts = _facts(6)
    lines = summarize_facts(facts)
    assert len(lines) == 1
    line = lines[0]
    assert line.values == tuple(f"nginx-{i}" for i in range(6))
    assert line.merged_from == tuple(f.id for f in facts), "merged_from 必须能回溯到每一条原事实"


def test_different_predicates_are_not_merged() -> None:
    facts = [_fact(1, predicate="server"), _fact(2, predicate="framework", value="flask")]
    assert len(summarize_facts(facts)) == 2


# ── B 步骤摘要 ≤5 行 + 更早只报计数 ───────────────────────────
def test_steps_capped_and_count_older() -> None:
    lines = summarize_steps(_events(20))
    assert len(lines) == STEP_LINES + 1, "5 行正文 + 1 行省略计数"
    assert lines[-1].startswith("... 更早 15 步")
    assert all(":" in line for line in lines[:-1])


def test_steps_keep_failures_visible() -> None:
    lines = summarize_steps(_events(3))
    assert any(line.endswith(":fail") for line in lines)


# ── C token 降 ≥30% ───────────────────────────────────────────
def test_reduction_is_at_least_thirty_percent() -> None:
    result = compress(facts=_facts(20), events=_events(20),
                      capsule_ids=[f"c-{i}" for i in range(5)], verdicts=VERDICTS)
    assert result.tokens_before > 0
    assert result.reduction >= 0.30, f"只降了 {result.reduction:.2%}"


def test_token_estimate_is_cheap_and_monotone() -> None:
    assert estimate_tokens("") == 1
    assert estimate_tokens("abcdefgh") == 2


# ── D 判定不受压缩影响 ────────────────────────────────────────
def test_verdict_fingerprint_unchanged_by_compression() -> None:
    from veripen_core.context import verdict_fingerprint

    result = compress(facts=_facts(5), events=_events(5), capsule_ids=[], verdicts=VERDICTS)
    assert result.verdicts_fingerprint == verdict_fingerprint(VERDICTS)


def test_fallback_truncation_also_keeps_verdicts() -> None:
    from veripen_core.context import verdict_fingerprint

    result = compress(facts=_facts(30), events=_events(30), capsule_ids=[],
                      verdicts=VERDICTS, summarize=False)
    assert result.mode == "truncated"
    assert result.dropped["facts"] == 20
    assert result.verdicts_fingerprint == verdict_fingerprint(VERDICTS)


# ── E 证据按需 ────────────────────────────────────────────────
def test_evidence_is_ids_only_by_default() -> None:
    result = compress(facts=[], events=[], capsule_ids=["c-1", "c-2"], verdicts={})
    assert result.evidence == ("c-1", "c-2")
    assert "BODY" not in result.text


def test_expand_loads_only_requested_capsules() -> None:
    loaded: list[str] = []

    def loader(cid: str) -> str:
        loaded.append(cid)
        return f"BODY:{cid}"

    got = expand(["c-1", "c-2", "c-3"], wanted=["c-2"], loader=loader)
    assert got == {"c-2": "BODY:c-2"}
    assert loaded == ["c-2"], "没点名的证据不许被加载"


# ── 顺序与确定性 ──────────────────────────────────────────────
def test_compression_is_deterministic() -> None:
    a = compress(facts=_facts(8), events=_events(8), capsule_ids=["c-1"], verdicts=VERDICTS)
    b = compress(facts=_facts(8), events=_events(8), capsule_ids=["c-1"], verdicts=VERDICTS)
    assert a.text == b.text


@pytest.mark.parametrize("count", [0, 1, 10])
def test_degenerate_inputs_do_not_crash(count: int) -> None:
    result = compress(facts=_facts(count), events=_events(count), capsule_ids=[], verdicts={})
    assert result.tokens_after >= 0
