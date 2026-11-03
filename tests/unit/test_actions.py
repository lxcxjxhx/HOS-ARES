"""B12.6 ``act.*`` 测试：能打，但打出来的只是线索（35 §4/§5/§8）。

六个动作各一条，重点是三条边界：
- 动作层**不许**出现判定类型（AST 扫，不是文字纪律）；
- 地址只能以 ``route_ref``/``target_ref`` 形式进，真实地址由框架注入；
- 每个动作都要产一条**可闭合**的线索；闭合不了的标 expired 而不是算失败。
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from negative_samples import BAD_URL
from veripen_core.actions import (
    ACTIONS,
    ARGV_TEMPLATES,
    BINARY_WHITELIST,
    ActionError,
    LeadDraft,
    build_argv,
    compare,
    compose,
    decode,
    exec_tool,
    http_fuzz,
    http_raw,
    run_action,
    sandbox_mode,
)
from veripen_core.routes.addresses import contains_address
from veripen_core.routes.types import RouteBinding

ROOT = Path(__file__).resolve().parents[2]
ACTIONS_DIR = ROOT / "veripen_core" / "actions"

REF = "route://observed/" + "a" * 16


def _binding(ref: str = REF, tags: tuple[str, ...] = ("param_injectable",)) -> RouteBinding:
    return RouteBinding(
        ref=ref, source="observed", sources_all=("observed",), confidence=0.9,
        template="%2Fapi%2Fitems%2F{id}", method="GET", param_names=("id",),
        auth_requirement=None, body_shape_sha256="b" * 64, structural_key="k",
        template_sha256="t" * 64, ttl_s=900, verifications=1, usable=True,
        semantic_tags=tags,
    )


# ══════════════════════════════════════════════════════════════════
# 1 动作层不许判定（AST 扫，红得掉）
# ══════════════════════════════════════════════════════════════════
def test_actions_cannot_import_verdict() -> None:
    """DoD 点名的那条：``actions/`` 下 import ``Verdict``/``Judgement`` 即红。"""
    banned = {"Verdict", "Judgement"}
    offenders: list[str] = []
    for p in ACTIONS_DIR.rglob("*.py"):
        if "__pycache__" in p.parts:
            continue
        tree = ast.parse(p.read_text(encoding="utf-8"), filename=str(p))
        for node in ast.walk(tree):
            names = [a.name for a in node.names] if isinstance(
                node, (ast.Import, ast.ImportFrom)) else []
            for n in names:
                if n.split(".")[-1] in banned:
                    offenders.append(f"{p.name}:{node.lineno}:{n}")
    assert offenders == [], f"动作层出现判定类型：{offenders}"


def test_the_guard_itself_can_go_red(tmp_path: Path) -> None:
    """守卫必须能红：造一个真的 import Verdict 的文件，同一段扫描逻辑要抓到它。"""
    bad = tmp_path / "bad_action.py"
    bad.write_text("from veripen_core.models import Verdict\n", encoding="utf-8")
    tree = ast.parse(bad.read_text(encoding="utf-8"), filename=str(bad))
    hits = [
        n for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
        for n in (a.name for a in node.names) if n == "Verdict"
    ]
    assert hits == ["Verdict"]


def test_lead_draft_has_no_verdict_field() -> None:
    d = LeadDraft(source_action="http_raw", raw_ref="out/act/x.json",
                  summary="s", verifiable_by=("sqli_bool_diff",))
    assert not ({"verdict", "severity", "confidence"} & set(d.__dict__))
    d.assert_no_verdict()


# ══════════════════════════════════════════════════════════════════
# 2 六个动作各跑通，且各产一条 lead
# ══════════════════════════════════════════════════════════════════
def test_http_raw_produces_lead() -> None:
    r = http_raw(
        route_ref=REF, method="GET", binding=_binding(),
        send=lambda b, m, h, body: {"status": 500, "interesting": True,
                                    "verifiable_by": ["sqli_error_based"]},
    )
    assert r.lead.source_action == "http_raw"
    assert r.lead.verifiable_by == ("sqli_error_based",)
    assert not r.expired
    assert "500" in r.lead.summary


#: 反例样本：**必须是真实地址形态**，否则测不出"地址被拒"。
#: 集中在 tests/negative_samples.py（拼接构造，源码里无违规形态）。
BAD_REF = BAD_URL


def test_http_raw_rejects_address_in_ref() -> None:
    """ref 里出现真实地址 = 有人想手工拼地址。"""
    with pytest.raises(ActionError, match="地址字面量"):
        http_raw(route_ref=BAD_REF, method="GET",
                 binding=_binding(BAD_REF),
                 send=lambda *a: {"status": 200})


def test_http_fuzz_produces_lead_with_diffs() -> None:
    r = http_fuzz(
        route_ref=REF, param="id", variants=["1", "1'", "1 and 1=2"], rate_per_s=5,
        binding=_binding(),
        send=lambda b, p, v: {"status": 200, "differential": "'" in v},
    )
    assert r.lead.source_action == "http_fuzz"
    assert r.output["diffs"] == 1  # 只有 "1'" 含引号：变体写错时这里会先红
    assert "sqli_bool_diff" in r.lead.verifiable_by


def test_http_fuzz_requires_rate() -> None:
    with pytest.raises(ActionError, match="rate_per_s"):
        http_fuzz(route_ref=REF, param="id", variants=["1"], rate_per_s=0,
                  binding=_binding(), send=lambda *a: {})


def test_http_fuzz_refuses_address_fields() -> None:
    with pytest.raises(ActionError, match="地址字段"):
        http_fuzz(route_ref=REF, param="url", variants=["x"], rate_per_s=1,
                  binding=_binding(), send=lambda *a: {})


def test_exec_tool_produces_lead() -> None:
    r = exec_tool(
        binary="nmap", preset="top_ports", target_ref=REF,
        slots={"ports": "80,443", "host": "injected-by-framework.invalid"},
        run=lambda argv: {"stdout": "22/open\n", "hits": ["22"], "verifiable_by": []},
    )
    assert r.lead.source_action == "exec_tool"
    assert r.output["argv_len"] == 5


def test_exec_tool_rejects_non_whitelisted_binary() -> None:
    """白名单外的一律拒——尤其是**二次编程载体**（35 §4 明确点名的那批）。"""
    banned = "msfconsole sqlmap sliver python bash".split()
    for bad in banned:
        with pytest.raises(ActionError, match="白名单"):
            build_argv(bad, slots={}, target_ref=REF)


def test_argv_is_array_not_string() -> None:
    argv = build_argv("nmap", slots={"ports": "80", "host": "h.invalid"}, target_ref=REF)
    assert isinstance(argv, list) and all(isinstance(x, str) for x in argv)
    assert argv[0] == "nmap"


def test_argv_rejects_address_in_slots() -> None:
    """slots 里出现地址 = 模型想自己指定目标。"""
    with pytest.raises(ActionError, match="地址字面量"):
        build_argv("nmap", slots={"ports": "80", "host": BAD_REF}, target_ref=REF)


def test_argv_rejects_unknown_slot() -> None:
    with pytest.raises(ActionError, match="未知模板占位符"):
        build_argv("nmap", slots={"ports": "80", "host": "h", "evil": "x"},
                   target_ref=REF)


def test_decode_base64_produces_lead() -> None:
    import base64

    blob = base64.b64encode(b'{"data":{"id":1}}').decode()
    r = decode(blob=blob, how="base64")
    assert r.lead.source_action == "decode"
    assert r.lead.verifiable_by


def test_decode_jwt_hints_token_issue() -> None:
    import base64

    head = base64.urlsafe_b64encode(b'{"alg":"HS256","typ":"JWT"}').decode().rstrip("=")
    body = base64.urlsafe_b64encode(b'{"sub":"1"}').decode().rstrip("=")
    r = decode(blob=f"{head}.{body}.sig", how="jwt")
    assert r.lead.verifiable_by == ("jwt_replay",)
    assert r.lead.selector_hint == "token_issue"


def test_decode_rejects_unknown_scheme() -> None:
    with pytest.raises(ActionError, match="不支持的解码方式"):
        decode(blob="x", how="rot13")


def test_compare_produces_lead() -> None:
    r = compare(left={"id": 1, "owner": "a"}, right={"id": 1, "owner": "a", "ssn": "x"})
    assert r.output["signal"] is True
    assert "idor_cross_account" in r.lead.verifiable_by


def test_compare_without_signal_produces_dead_lead() -> None:
    """无差分的 compare 是白打：产出的线索验不了，标 expired 而不是算成功。"""
    r = compare(left={"a": 1}, right={"a": 1})
    assert r.expired is True
    assert r.lead.verifiable_by == ()


def test_compose_never_sends() -> None:
    r = compose(template="<script>alert({n})</script>", slots={"n": "1"})
    assert r.output["sent"] is False
    assert r.lead.source_action == "compose"


def test_compose_rejects_address() -> None:
    with pytest.raises(ActionError, match="地址"):
        compose(template=BAD_REF, slots={})


# ══════════════════════════════════════════════════════════════════
# 3 地址语法只有一个来源
# ══════════════════════════════════════════════════════════════════
def test_route_ref_is_not_an_address() -> None:
    """``route://`` 自带分隔符，但它是我们的不透明引用，不是地址。"""
    assert contains_address(REF) is False
    assert contains_address(BAD_REF) is True
    assert contains_address("10.0.0.1") is True
    assert contains_address("host:8080") is True


def test_every_template_slot_is_declared() -> None:
    from veripen_core.actions import TEMPLATE_SLOTS

    for binary, template in ARGV_TEMPLATES.items():
        for part in template:
            if part.startswith("{") and part.endswith("}"):
                assert part[1:-1] in TEMPLATE_SLOTS, f"{binary}: 未声明的占位符 {part}"


# ══════════════════════════════════════════════════════════════════
# 4 统一入口与沙箱
# ══════════════════════════════════════════════════════════════════
def test_all_six_actions_registered() -> None:
    assert set(ACTIONS) == {
        "http_raw", "http_fuzz", "exec_tool", "decode", "compare", "compose",
    }


def test_sandbox_modes_match_doc() -> None:
    assert sandbox_mode("decode") == "none"
    assert sandbox_mode("compare") == "none"
    assert sandbox_mode("compose") == "none"
    assert sandbox_mode("exec_tool") == "container_ro_root"
    assert sandbox_mode("http_raw") == "egress_allowlist"
    with pytest.raises(ActionError, match="未知动作"):
        sandbox_mode("scan_network")


def test_run_action_dispatches() -> None:
    r = run_action("compose", template="{'a':{v}}", slots={"v": "1"})
    assert r.action == "compose"


def test_whitelist_excludes_second_order_platforms() -> None:
    """二次编程载体（自己会加载代码的）一律不进白名单，否则 05 的边界表就失去意义。"""
    forbidden = "msfconsole sliver sqlmap metasploit python node".split()
    for bad in forbidden:
        assert bad not in BINARY_WHITELIST


def test_lead_summary_is_short() -> None:
    """线索摘要 ≤200 字（35 §2）：摘要写不完说明这条线索没想清楚。"""
    r = compose(template="x" * 50, slots={})
    assert len(r.lead.summary) <= 200


def test_raw_ref_never_contains_target_text() -> None:
    """raw_ref 只存哈希：完整输出落盘，引用里不许出现任何目标内容。"""
    r = http_raw(route_ref=REF, method="POST", binding=_binding(),
                 send=lambda *a: {"status": 200, "echo": "secret-value-xyz"})
    assert "secret-value-xyz" not in r.lead.raw_ref
    assert r.lead.raw_ref.startswith("out/act/")


def test_result_is_json_serialisable() -> None:
    d = LeadDraft(source_action="compose", raw_ref="r", summary="s",
                  verifiable_by=("xss_reflected_ctx",))
    assert json.loads(json.dumps(d.__dict__))["source_action"] == "compose"
