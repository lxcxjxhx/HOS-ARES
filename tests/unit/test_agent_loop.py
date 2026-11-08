"""B10 agent 循环测试（08 §1–§3 + 21 §2 B10 的 DoD）。

DoD 逐条：
- `max_steps=5` 正常收敛并标 budget_exhausted
- 模型名写错 → 降级 scripted planner 仍完成
- 非法 JSON 回喂 ≤3 次
- 给模型"直接访问某地址"的诱导 → 无法执行
- `max_rediscovery=0` 仍能完成
"""

from __future__ import annotations

import json

import pytest
from conftest import Lab

from veripen_core.agent import (
    AgentState,
    Decision,
    DiscoveryTier,
    FailoverDecider,
    Loop,
    ModelDecider,
    ScriptedDecider,
    classify_discovery,
    pick_next,
)
from veripen_core.budget import Budget, BudgetKind, BudgetLimits
from veripen_core.tools.registry import (
    RETIRED_TOOLS,
    TOOL_CALLS_PER_STEP,
    ToolCall,
    bound_and_store,
    dispatch,
    tool_schemas,
    validate_call,
)
from veripen_oracles import ALL_ORACLES
from veripen_oracles import policy


# ── 测试用的最小 runtime（进程内靶场）──────────────────────────────
class LabRuntime:
    def __init__(self, lab: Lab, oracle_map: dict[str, str]) -> None:
        self.lab = lab
        self.oracle_map = oracle_map  # oracle_id → selector tag
        self.calls: list[str] = []
        self.rediscovery = 0

    def list_assets(self):
        """资产表（08 §2.1）。lab 只有一个资产，但**字段形状要是真的**：
        id / kind / criticality 三项，模型据此决定先打谁。"""
        from lab.app import assets_json

        a = assets_json()
        return [{"id": a["asset_id"], "kind": "web", "criticality": "high"}]

    def list_routes(self, selector: str | None):
        from lab.routes import routeset

        rs = routeset()
        return [
            {"ref": b.ref, "tags": list(b.semantic_tags), "confidence": b.confidence,
             "source": b.source}
            for b in rs.bindings
            if selector is None or selector in b.semantic_tags
        ]

    def run_oracle(self, oracle_id: str, route_ref: str, params):
        self.calls.append(oracle_id)
        tag = self.oracle_map.get(oracle_id)
        if tag is None:
            return {"verdict": "BLOCKED", "reason": "route_unresolved", "route_strength": "contract"}
        from veripen_oracles.common.harness import run as run_oracle

        cls = next((c for c in ALL_ORACLES if c.meta.id == oracle_id), None)
        if cls is None:
            return {"verdict": "BLOCKED", "reason": "unknown_oracle", "route_strength": "contract"}
        result = run_oracle(
            cls(),
            self.lab.binding(tag),
            self.lab.transport,
            symbols=self.lab.symbols,
            nonce=f"n-{oracle_id}",
            token=self.lab.token,
        )
        return {
            "verdict": result.verdict.value,
            "reason": result.reason,
            "route_strength": "contract",
            # 与真实 runtime 一致（B152）：不封 capsule 就没有 id 可给，别编一个
            "capsule_id": None,
            "evidence": "not_sealed",
        }

    def rediscover(self, reason: str):
        self.rediscovery += 1
        return {"routeset_sha256": "0" * 64, "added": 0, "removed": 0, "changed": 0, "unresolved": []}

    def request_approval(self, action_class: str, reason: str):
        return {"approval_id": "ap-1", "state": "pending"}


ORACLE_MAP = {
    "sqli_bool_diff": "param_injectable",
    "sqli_error_based": "param_injectable",
    "idor_cross_account": "object_read",
    "ssrf_oob": "url_fetch",
    "open_redirect": "redirect_entry",
    "path_traversal": "file_read",
    "xss_reflected_ctx": "echo_param",
    "bola_functional": "admin_function",
    "mass_assignment": "profile_write",
    "jwt_suite": "protected_read",
}


def _state(oracles=None) -> AgentState:
    ids = tuple(oracles or ORACLE_MAP)
    return AgentState(pending=ids, strong=(), wave_order=ids)


# ── 1 discovery 三档 ───────────────────────────────────────────────
def test_discovery_complete_partial_empty(vulnerable: Lab) -> None:
    from lab.routes import routeset

    rs = routeset()
    sel = {"sqli_bool_diff": ("param_injectable",), "bogus": ("no_such_tag",)}
    outcome = classify_discovery(oracle_ids=["sqli_bool_diff"], routeset=rs, selectors=sel)
    assert outcome.tier is DiscoveryTier.COMPLETE

    partial = classify_discovery(oracle_ids=["sqli_bool_diff", "bogus"], routeset=rs, selectors=sel)
    assert partial.tier is DiscoveryTier.PARTIAL
    assert partial.blocked == {"bogus": "route_unresolved"}
    assert "bogus" in partial.report_line()

    empty = classify_discovery(oracle_ids=["bogus"], routeset=rs, selectors=sel)
    assert empty.tier is DiscoveryTier.EMPTY
    assert empty.should_run is False, "全空必须立刻收尾"
    assert "本次未获得任何可测面" in empty.report_line()


def test_bindings_now_carry_semantic_tags(vulnerable: Lab) -> None:
    """标签必须能传到 binding 上，否则 discovery 永远拿不到面。"""
    from lab.routes import routeset

    rs = routeset()
    assert any(b.semantic_tags for b in rs.bindings)


# ── 2 条件表选面 ───────────────────────────────────────────────────
def test_pick_next_priority_order() -> None:
    oid, reason = pick_next(AgentState(pending=("a",), strong=("b",), wave_order=("c",)))
    assert (oid, reason) == ("a", "resume_pending")

    oid, reason = pick_next(AgentState(pending=(), strong=("b",), wave_order=("c",)))
    assert (oid, reason) == ("b", "strong_evidence_first")

    oid, reason = pick_next(AgentState(pending=(), strong=(), only_weak=True, wave_order=("c",)))
    assert oid is None and reason == "need_rediscovery", "选不出是合法结果，触发重发现"


# ── 3 循环：正常收敛 / 预算刹车 ────────────────────────────────────
def test_loop_converges(vulnerable: Lab) -> None:
    runtime = LabRuntime(vulnerable, ORACLE_MAP)
    loop = Loop(runtime=runtime, decider=ScriptedDecider(), goal="demo")
    result = loop.run(_state(["sqli_bool_diff", "idor_cross_account"]))
    assert result.decision is Decision.CONVERGED
    assert result.verdicts["sqli_bool_diff"] == "VERIFIED"
    assert len(result.step_entries) >= 2


def test_max_steps_marks_budget_exhausted(vulnerable: Lab) -> None:
    runtime = LabRuntime(vulnerable, ORACLE_MAP)
    loop = Loop(runtime=runtime, decider=ScriptedDecider(), goal="demo", max_steps=5)
    result = loop.run(_state(list(ORACLE_MAP)))
    assert result.decision is Decision.BUDGET
    assert result.reason == "budget_exhausted:steps"
    # 步数上限也要落进 budget_exhausted：这是"没跑完"的如实标注，不是"未发现"
    assert result.budget["budget_exhausted"] == "steps", "必须如实标注，不许写成'未发现'"


def test_no_oracles_short_circuits(vulnerable: Lab) -> None:
    runtime = LabRuntime(vulnerable, ORACLE_MAP)
    loop = Loop(runtime=runtime, decider=ScriptedDecider())
    result = loop.run(AgentState())
    assert result.decision is Decision.NOTHING_TO_DO


def test_converges_after_three_idle_steps(vulnerable: Lab) -> None:
    class Idle:
        name = "idle"

        def decide(self, goal, state, tools):  # noqa: ANN001, ANN201
            return ToolCall("record_note", {"text": "noop"})

    runtime = LabRuntime(vulnerable, ORACLE_MAP)
    loop = Loop(runtime=runtime, decider=Idle())
    result = loop.run(_state(["sqli_bool_diff"]))
    assert result.decision is Decision.CONVERGED
    assert result.reason == "no_new_facts_3_steps"


# ── 4 降级链：模型挂了流程仍走完 ───────────────────────────────────
def test_bad_model_output_falls_back_to_scripted(vulnerable: Lab) -> None:
    def broken_client(goal, ctx):  # noqa: ANN001, ANN202
        return {"tool": "run_oracle", "args": {"bogus_field": True}}  # 稳定触发 schema 错误

    decider = FailoverDecider([ModelDecider(broken_client), ScriptedDecider()])
    runtime = LabRuntime(vulnerable, ORACLE_MAP)
    loop = Loop(runtime=runtime, decider=decider, goal="demo")

    result = loop.run(_state(["sqli_bool_diff"]))
    assert decider.level == 1, "连续 3 次 schema 错误必须降级"
    assert result.verdicts.get("sqli_bool_diff") == "VERIFIED", "降级后仍必须完成"


def test_schema_errors_capped_at_three(vulnerable: Lab) -> None:
    calls = {"n": 0}

    def bad(goal, ctx):  # noqa: ANN001, ANN202
        calls["n"] += 1
        return {"tool": "run_oracle", "args": {"skip_guard": True}}

    decider = FailoverDecider([ModelDecider(bad), ScriptedDecider()])
    loop = Loop(runtime=LabRuntime(vulnerable, ORACLE_MAP), decider=decider, max_steps=10)
    loop.run(_state(["sqli_bool_diff"]))
    assert calls["n"] <= 3, "非法 JSON/字段只能回喂 3 次"


# ── 5 诱导：让模型"直接访问某地址" → 无法执行 ──────────────────────
def test_no_tool_can_take_an_address(vulnerable: Lab) -> None:
    schemas = tool_schemas()
    for name, schema in schemas.items():
        for field in "url host port path address".split():
            assert field not in schema["required"], f"{name} 出现了地址字段 {field}"


def test_probe_asset_is_retired() -> None:
    from veripen_core.tools.registry import ToolResult

    assert "probe_asset" in RETIRED_TOOLS
    result = validate_call(ToolCall("probe_asset", {"asset_id": "web-01"}))
    assert isinstance(result, ToolResult)
    assert result.error_class == "unknown_tool"
    assert "拼地址" in result.hint or "地址" in result.hint


def test_extra_outer_field_is_rejected() -> None:
    """模型把 skip_guard 塞进外层 → 直接拒，不透传。"""
    from veripen_core.tools.registry import ToolResult

    result = validate_call(ToolCall("run_oracle", {"oracle_id": "a", "route_ref": "b", "skip_guard": True}))
    assert isinstance(result, ToolResult)
    assert result.error_class == "schema"
    assert "skip_guard" in result.hint


def test_inner_params_go_to_oracle_schema_only(vulnerable: Lab) -> None:
    """外层不解析 params：内层交给 oracle 的 params_schema。"""
    runtime = LabRuntime(vulnerable, ORACLE_MAP)
    ok = dispatch(ToolCall("run_oracle", {"oracle_id": "sqli_bool_diff", "route_ref": "r",
                                          "params": {"repeat": 2}}), runtime)
    assert ok.ok


# ── 6 预算与输出归一化 ────────────────────────────────────────────
def test_budget_hard_stop_and_80_percent_behaviour() -> None:
    budget = Budget(limits=BudgetLimits(steps=10, rediscovery=2))
    for _ in range(8):
        budget.spend_step()
    assert budget.at_80(BudgetKind.STEPS)
    assert budget.rediscover_allowed() is False, "80% 后禁重发现"
    for _ in range(3):
        budget.spend_step()
    assert budget.hit() is BudgetKind.STEPS
    assert budget.as_dict()["budget_exhausted"] == "steps"


def test_rediscovery_quota_zero_still_completes(vulnerable: Lab) -> None:
    budget = Budget(limits=BudgetLimits(rediscovery=0))
    loop = Loop(runtime=LabRuntime(vulnerable, ORACLE_MAP), decider=ScriptedDecider(), budget=budget)
    result = loop.run(_state(["sqli_bool_diff"]))
    assert result.verdicts.get("sqli_bool_diff") == "VERIFIED", "配额 0 也能完成（不重发现而已）"


def test_output_truncation_keeps_tail(tmp_path) -> None:
    """尾部优先：失败原因在最后几行，头部截断会把'为什么失败'整段切掉。"""
    body = "\n".join(f"line-{i}" for i in range(2000)) + "\nEXIT_CODE=7\nERROR: connection refused\n"
    out = bound_and_store(body, limit=4096, store_dir=tmp_path)
    assert out.truncated
    assert "ERROR: connection refused" in out.text, "尾部必须保住"
    assert out.persisted_ref and out.persisted_ref.startswith("out/tool-results/")
    assert out.persisted_size is not None and out.persisted_size > 4096
    assert out.dropped_lines > 0


def test_small_output_not_truncated() -> None:
    out = bound_and_store("hello", limit=4096)
    assert not out.truncated and out.persisted_ref is None


def test_tool_calls_per_step_is_one() -> None:
    assert TOOL_CALLS_PER_STEP == 1, "一步一调用是硬上限，不是软约定"


# ── 7 默认关闭的 oracle 即使被模型点名也必须拦 ────────────────────
def test_disabled_oracle_blocked_at_startup() -> None:
    decision = policy.check_enabled(list(ORACLE_MAP))
    assert "mass_assignment" in decision.blocked


# ── 8 多面结论合并：任何一面成立，这个 oracle 就成立（B156）──────────
class _VerdictRuntime:
    """按预设顺序回 verdict 的最小 runtime：只测合并，不跑判定。"""

    def __init__(self, verdicts: list[str]) -> None:
        self.queue = list(verdicts)
        self.calls = 0

    def run_oracle(self, oracle_id: str, route_ref: str, params: object) -> dict[str, object]:
        self.calls += 1
        return {"verdict": self.queue.pop(0), "reason": "test", "route_strength": "contract"}

    def list_assets(self) -> list[dict[str, str]]:
        return []

    def list_routes(self, selector: str | None) -> list[dict[str, str]]:
        return []


def _run_two_faces(verdicts: list[str]) -> dict[str, str]:
    from veripen_core.tools.registry import ToolCall

    loop = Loop(runtime=_VerdictRuntime(verdicts), decider=object(), goal="g", max_steps=5)
    call = ToolCall("run_oracle", {"oracle_id": "sqli_bool_diff", "route_ref": ""})
    loop._dispatch(call)  # noqa: SLF001 —— 合并逻辑就在这里，直接测它
    loop._dispatch(call)  # noqa: SLF001
    return loop.verdicts


def test_verified_face_is_not_erased_by_a_later_refuted_face() -> None:
    """A 面判成立、B 面判否 → 这个 oracle 仍然成立。

    修前是"后一条面说了算"：B 面判否之后，hunt 汇总里的这条洞消失，
    而控制台按面看仍显示 1 条 reported——同一份数据两个出口对不上。
    """
    assert _run_two_faces(["VERIFIED", "REFUTED"])["sqli_bool_diff"] == "VERIFIED"


def test_verified_face_survives_a_later_blocked_face() -> None:
    assert _run_two_faces(["VERIFIED", "BLOCKED"])["sqli_bool_diff"] == "VERIFIED"


def test_later_verified_still_wins() -> None:
    assert _run_two_faces(["REFUTED", "VERIFIED"])["sqli_bool_diff"] == "VERIFIED"


def test_refuted_then_blocked_still_reports_blocked() -> None:
    """只有 VERIFIED 是粘的：没成立的结论仍按后一条面记（不替它升级）。"""
    assert _run_two_faces(["REFUTED", "BLOCKED"])["sqli_bool_diff"] == "BLOCKED"


# ── 9 finish 的自述要留下来，但不能变成结论（B158）──────────────
class _FinishDecider:
    name = "finish-only"

    def __init__(self, summary: str, unresolved: list[str]) -> None:
        self.summary = summary
        self.unresolved = unresolved

    def decide(self, goal: str, state: object, tools: object) -> ToolCall:
        return ToolCall("finish", {"summary": self.summary, "unresolved": self.unresolved})


def _finish_once(summary: str, unresolved: list[str]) -> object:
    state = AgentState(pending=["sqli_bool_diff"], strong=(), wave_order=["sqli_bool_diff"])
    loop = Loop(
        runtime=_VerdictRuntime([]),
        decider=_FinishDecider(summary, unresolved),  # type: ignore[arg-type]
        goal="g",
        max_steps=3,
    )
    return loop.run(state)


def test_finish_summary_and_unresolved_are_kept() -> None:
    """模型最后说的话要留在结果里：早先被 `_finish` 整个丢掉。"""
    result = _finish_once("找到 2 条，rate limit 没测", ["rate_limit_absent"])
    assert result.decision is Decision.CONVERGED
    assert result.agent_summary == "找到 2 条，rate limit 没测"
    assert result.agent_unresolved == ("rate_limit_absent",)


def test_finish_summary_is_not_a_verdict() -> None:
    """自述**不能**产生结论：verdicts 仍为空，覆盖账本照旧按逐面记录算。"""
    result = _finish_once("我确认了 sqli_bool_diff 成立", [])
    assert result.verdicts == {}, "模型说成立不算成立"
    assert result.step_entries, "步骤要留痕"
    assert result.step_entries[-1].tool == "finish"


def test_finish_summary_is_capped() -> None:
    """自述再长也不能变成一份没上限的附件。"""
    result = _finish_once("x" * 5000, [f"u{i}" for i in range(50)])
    assert len(result.agent_summary) <= 500
    assert len(result.agent_unresolved) <= 20
