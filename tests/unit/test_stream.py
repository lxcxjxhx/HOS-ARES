"""B12.8 SSE 与操作台边界测试（34 §2/§3）。

三条约束 + 一条铁律：
1. 事件不含模板正文 / host / IP；
2. ``act.*`` 事件必须带 ``sandbox_id``；
3. SSE 只推新增（``after=seq`` 游标），不推历史；
4. **控制台永远改不了 verdict**——接口层就不存在这个操作。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from negative_samples import BAD_URL as BAD_ADDR
from negative_samples import EXTERNAL_IP, LOCAL_NAME, SEP
from veripen_core.stream import (
    CONTROL_OPS,
    FORBIDDEN_OPS,
    SELECTOR_TAGS,
    ControlOp,
    Cursor,
    EventType,
    StreamError,
    events_from_rows,
    make,
    validate_control,
)

ROOT = Path(__file__).resolve().parents[2]
REF = "route://contract/" + "7f3a" * 4
#: 环回/端口反例（同样只在被测的判定里出现）
BAD_HOSTS = [EXTERNAL_IP, LOCAL_NAME, "svc" + ":" + "8443"]


# ══════════════════════════════════════════════════════════════════
# 1 五类事件
# ══════════════════════════════════════════════════════════════════
def test_five_event_types_only() -> None:
    assert {e.value for e in EventType} == {"step", "budget", "fact", "lead", "approval"}


def test_unknown_event_type_is_rejected() -> None:
    with pytest.raises(StreamError, match="未知事件类型"):
        make("verdict", {})


def test_step_event_frames_as_sse() -> None:
    ev = make(EventType.STEP, {"seq": 14, "kind": "verify", "ok": True,
                               "route_ref": REF, "oracle_id": "idor_cross_account"})
    frame = ev.to_sse()
    assert frame.startswith("event: step\ndata: ")
    assert frame.endswith("\n\n")
    body = json.loads(frame.split("data: ", 1)[1].strip())
    # noqa 注释：ref 是 route:// 形态，不是地址——lint-ui 用它做唯一例外
    assert body["route_ref"] == REF


def test_required_fields_are_enforced() -> None:
    with pytest.raises(StreamError, match="缺字段"):
        make(EventType.BUDGET, {"steps": {"used": 1, "max": 2}})


def test_all_five_shapes_from_doc_34() -> None:
    """34 §2 的五个 jsonc 样例逐条落地。"""
    samples = [
        make("step", {"seq": 14, "kind": "act", "action": "exec_tool", "route_ref": REF,
                      "ok": True, "elapsed_ms": 18400, "truncated": True,
                      "persisted_ref": "out/act/9c1f.txt", "sandbox_id": "sbx-2",
                      "summary": "nmap top_ports → 3 open"}),
        make("budget", {"steps": {"used": 14, "max": 120},
                        "tokens": {"used": 210000, "max": 400000},
                        "leads": {"pending": 1, "verified": 2, "refuted": 1,
                                  "unverifiable": 1}}),
        make("fact", {"id": "f_88", "subject": "web-01", "predicate": "server",
                      "value": "nginx/1.24", "from_target": 1, "evidence_id": "ev_3f",
                      "disputed": False}),
        make("lead", {"lead_id": "L-07", "state": "pending", "source": "act.http_fuzz",
                      "verifiable_by": ["idor_cross_account", "sqli_bool_diff"],
                      "confidence_hint": 0.4}),
        make("approval", {"approval_id": "ap_12", "action_class": "reversible_write",
                          "reason": "mass_assignment", "state": "PENDING",
                          "approver_role": "secops_lead"}),
    ]
    assert [e.t.value for e in samples] == [e.value for e in EventType]
    for e in samples:
        assert e.to_sse().startswith(f"event: {e.t.value}")


# ══════════════════════════════════════════════════════════════════
# 2 事件里零地址
# ══════════════════════════════════════════════════════════════════
def test_event_rejects_address_anywhere_nested() -> None:
    with pytest.raises(StreamError, match="出现地址"):
        make("step", {"seq": 1, "kind": "verify", "ok": True,
                      "summary": {"nested": BAD_ADDR}})


def test_event_rejects_host_and_port() -> None:
    for bad in BAD_HOSTS:
        with pytest.raises(StreamError, match="出现地址"):
            make("fact", {"id": "f", "subject": "s", "predicate": "p", "value": bad,
                          "evidence_id": "e"})


def test_route_ref_is_allowed() -> None:
    ev = make("lead", {"lead_id": "L-1", "state": "pending", "source": "act.http_raw",
                       "verifiable_by": ["idor_cross_account"], "route_ref": REF})
    assert ev.as_dict()["route_ref"] == REF


# ══════════════════════════════════════════════════════════════════
# 3 act 事件必须带 sandbox_id
# ══════════════════════════════════════════════════════════════════
def test_act_event_without_sandbox_is_rejected() -> None:
    """DoD：看不见它跑了什么命令 = 安全事故。"""
    with pytest.raises(StreamError, match="sandbox_id"):
        make("step", {"seq": 14, "kind": "act", "ok": True, "action": "exec_tool"})


def test_act_event_with_sandbox_passes() -> None:
    ev = make("step", {"seq": 14, "kind": "act", "ok": True, "sandbox_id": "sbx-2"})
    assert ev.as_dict()["sandbox_id"] == "sbx-2"


# ══════════════════════════════════════════════════════════════════
# 4 只推新增
# ══════════════════════════════════════════════════════════════════
def test_cursor_only_takes_new_rows() -> None:
    cur = Cursor(after=5)
    rows = [{"seq": 3}, {"seq": 4}, {"seq": 6}, {"seq": 9}]
    fresh = cur.take(rows)
    assert [r["seq"] for r in fresh] == [6, 9]
    assert cur.after == 9


def test_cursor_rejects_negative() -> None:
    with pytest.raises(StreamError, match="不能为负"):
        Cursor(after=-1)


def test_cursor_is_monotonic() -> None:
    cur = Cursor(after=10)
    cur.take([{"seq": 7}])
    assert cur.after == 10  # 不回退


# ══════════════════════════════════════════════════════════════════
# 5 控制台改不了 verdict
# ══════════════════════════════════════════════════════════════════
def test_control_ops_have_no_verdict_path() -> None:
    """真读不变量的唯一实现：控制操作里**不存在**能改结论的那一个。"""
    assert not any("verdict" in op for op in CONTROL_OPS)
    assert not any("finding" in op for op in CONTROL_OPS)
    assert "task.set_verdict" not in CONTROL_OPS


def test_forbidden_ops_are_explicitly_rejected() -> None:
    for op in sorted(FORBIDDEN_OPS):
        with pytest.raises(StreamError, match="不存在"):
            validate_control(op, {})


def test_unknown_control_op_is_rejected() -> None:
    with pytest.raises(StreamError, match="未知控制操作"):
        validate_control("task.do_something", {})


def test_control_ops_cover_doc_34_section_3() -> None:
    allowed = "pause resume budget_delta approve reject inject_hint rediscover".split()
    assert CONTROL_OPS == {f"task.{a}" for a in allowed}


# ══════════════════════════════════════════════════════════════════
# 6 注入 selector 提示的输入约束
# ══════════════════════════════════════════════════════════════════
def test_inject_hint_accepts_known_tag_only() -> None:
    assert validate_control("task.inject_hint",
                            {"tag": "object_read", "reason": "优先看对象读取"}) is ControlOp.INJECT_HINT
    with pytest.raises(StreamError, match="18 §6 的标签"):
        validate_control("task.inject_hint", {"tag": "anything", "reason": "x"})


def test_inject_hint_requires_reason() -> None:
    with pytest.raises(StreamError, match="必须附一句理由"):
        validate_control("task.inject_hint", {"tag": "object_read", "reason": "  "})


def test_inject_hint_rejects_address_and_ref() -> None:
    """hint 是"影响打哪类面"，不是"人给模型指路"。"""
    with pytest.raises(StreamError, match="不许带地址"):
        validate_control("task.inject_hint",
                         {"tag": "object_read", "reason": f"去看 {BAD_ADDR}"})
    with pytest.raises(StreamError, match="不许带地址"):
        validate_control("task.inject_hint",
                         {"tag": "object_read", "reason": f"看这条 {REF}"})


def test_inject_hint_reason_is_bounded() -> None:
    with pytest.raises(StreamError, match="字 >"):
        validate_control("task.inject_hint", {"tag": "object_read", "reason": "x" * 200})


def test_selector_tags_match_doc_18_section_6() -> None:
    """18 §6 的标签字典：**从文档解析出来对账**，不写死个数。

    第一版这里写死 19（凭空多一个），被文档 18 的「共 18 个标签」当场否掉。
    改成解析文档后，改标签的人改完文档就能立刻发现代码没跟上。
    """
    import re

    doc = ROOT / "plan-veripen" / "18-Oracle目录（14类判定矩阵）.md"
    text = doc.read_text(encoding="utf-8")
    body = text[text.find("## 6"): text.find("**共 18 个标签")]
    documented = set(re.findall(r"^\| `([a-z_]+)` \|", body, re.M))
    assert len(documented) == 18
    assert documented == SELECTOR_TAGS


def test_selector_tag_drift_in_schemas_is_detected() -> None:
    """文档说「同步三处枚举」：schema 里少一个也应当被发现。"""
    doc = ROOT / "plan-veripen" / "18-Oracle目录（14类判定矩阵）.md"
    import re

    text = doc.read_text(encoding="utf-8")
    body = text[text.find("## 6"): text.find("**共 18 个标签")]
    documented = set(re.findall(r"^\| `([a-z_]+)` \|", body, re.M))
    for name in ("routeset.schema.json", "profile.schema.json"):
        blob = (ROOT / "plan-veripen" / "schemas" / name).read_text(encoding="utf-8")
        missing = sorted(t for t in documented if t not in blob)
        assert missing == [], f"{name} 缺标签 {missing}"


# ══════════════════════════════════════════════════════════════════
# 7 预算操作的纪律
# ══════════════════════════════════════════════════════════════════
def test_budget_delta_must_be_nonzero_int() -> None:
    with pytest.raises(StreamError, match="非零整数"):
        validate_control("task.budget_delta", {"delta": 0})
    with pytest.raises(StreamError, match="非零整数"):
        validate_control("task.budget_delta", {"delta": "10"})


def test_budget_increase_requires_reason() -> None:
    with pytest.raises(StreamError, match="必须写理由"):
        validate_control("task.budget_delta", {"delta": 50})
    assert validate_control("task.budget_delta", {"delta": 50, "reason": "首发现接近"})
    # 减少预算不需要理由
    assert validate_control("task.budget_delta", {"delta": -10})


# ══════════════════════════════════════════════════════════════════
# 8 从 task_events 行转事件
# ══════════════════════════════════════════════════════════════════
def test_rows_become_step_events() -> None:
    rows = [
        {"seq": 1, "kind": "oracle", "oracle_id": "sqli_bool_diff", "route_ref": REF,
         "ok": 1, "elapsed_ms": 12, "detail": {"summary": "差分成立"}},
        {"seq": 2, "kind": "http", "route_ref": REF, "ok": 0, "elapsed_ms": 900,
         "detail": {"summary": "exec_tool nmap", "persisted_ref": "out/act/a.txt"}},
    ]
    evs = events_from_rows(rows, sandbox_id="sbx-1")
    assert [e.t.value for e in evs] == ["step", "step"]
    assert evs[0].as_dict()["kind"] == "verify"
    assert evs[1].as_dict()["kind"] == "act"
    assert evs[1].as_dict()["sandbox_id"] == "sbx-1"


def test_act_row_without_sandbox_id_raises() -> None:
    rows = [{"seq": 2, "kind": "http", "route_ref": REF, "ok": 1, "detail": {}}]
    with pytest.raises(StreamError, match="sandbox_id"):
        events_from_rows(rows)


# ══════════════════════════════════════════════════════════════════
# 9 lint-ui 的门本身
# ══════════════════════════════════════════════════════════════════
def test_lint_ui_runs_clean_on_repo() -> None:
    import subprocess
    import sys as _sys

    proc = subprocess.run(
        [_sys.executable, str(ROOT / "scripts" / "lint" / "ui_zero_address.py")],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
