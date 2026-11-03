"""B12.15 ``shell.run`` 与强制代理测试（38 §4，05 §8.5）。

五道闸各自独立可测，其中两条是 DoD 点名的：
- **脚本含 URL → 静态拒**；
- **容器内直连外部 IP 失败、走代理成功**。

最后一条不能靠"我们很小心"，要靠网络模型本身：容器里除了回环代理端口，没有别的路。

反例样本集中放在 ``tests/negative_samples.py``（拼接构造，源码里没有违规形态）。
"""

from __future__ import annotations

import pytest

from negative_samples import (
    ABS_DIR,
    BAD_URL,
    EXTERNAL_IP,
    LOOPBACK,
    LOCAL_NAME,
    PROXY_PREFIX,
    SEP,
)
from veripen_core.workbench.shell import (
    ACTION_REQUIREMENTS,
    ADDRESS_PLACEHOLDERS,
    MAX_SCRIPT_BYTES,
    ApprovalPolicy,
    GateRejected,
    Interp,
    Risk,
    SandboxMode,
    SandboxNetwork,
    ShellRequest,
    WorkbenchPolicy,
    check_risk,
    describe_gates,
    policy_risk,
    run_shell,
    scan_script,
)


def _exec_ok(script, interp, cwd, timeout, env):  # noqa: ANN001
    return 0, "ok", ""


def _policy(**kw) -> WorkbenchPolicy:  # noqa: ANN003
    base = {"sandbox_mode": SandboxMode.WORKSPACE_WRITE,
            "approval_policy": ApprovalPolicy.ON_RISK}
    base.update(kw)
    return WorkbenchPolicy(**base)


def _req(script: str, **kw) -> ShellRequest:  # noqa: ANN003
    base = {"script": script, "interpreter": Interp.PYTHON, "risk_declared": Risk.MEDIUM}
    base.update(kw)
    return ShellRequest(**base)  # type: ignore[arg-type]


# ══════════════════════════════════════════════════════════════════
# ② 静态扫描：脚本含 URL → 拒
# ══════════════════════════════════════════════════════════════════
def test_script_with_url_is_rejected() -> None:
    """DoD 点名：脚本含 URL → 静态拒。"""
    hits = scan_script(f'import os\nx = "{BAD_URL}"\n')
    assert any(h.rule == "S-URL" for h in hits)


def test_script_with_ip_is_rejected() -> None:
    for host in (EXTERNAL_IP, LOOPBACK, LOCAL_NAME):
        assert any(h.rule == "S-URL" for h in scan_script(f'x = "{host}"')), host


def test_script_with_dangerous_calls_is_rejected() -> None:
    cases = {
        "os.system('ls')": "os.system",
        "import subprocess": "subprocess",
        "eval('1+1')": "eval",
        "exec('x=1')": "exec",
        "import ctypes": "ctypes",
    }
    for src, name in cases.items():
        hits = scan_script(src)
        assert any(h.rule == "S-DANGER" and h.detail == name for h in hits), src


def test_direct_socket_is_rejected() -> None:
    hits = scan_script("import socket\ns = socket.socket()")
    assert any(h.rule == "S-NET" for h in hits)


def test_proxy_placeholder_is_allowed() -> None:
    """脚本唯一允许出现的"地址"是代理占位符。"""
    src = 'proxy = "PROXY_LOOPBACK"\nprint(proxy)'
    assert scan_script(src) == []


def test_comment_mentioning_details_is_not_a_violation() -> None:
    """注释里提一句不该拦人。"""
    assert scan_script("# see the upstream docs for details\nprint(1)") == []


def test_scan_is_enforced_by_run_shell() -> None:
    with pytest.raises(GateRejected, match="静态扫描"):
        run_shell(_req(f'x = "{BAD_URL}"'), policy=_policy(), executor=_exec_ok)


# ══════════════════════════════════════════════════════════════════
# ④ 沙箱网络模型：直连失败、走代理成功
# ══════════════════════════════════════════════════════════════════
def test_direct_connect_to_external_ip_fails() -> None:
    """DoD 点名：容器内直连外部 IP 失败。"""
    net = SandboxNetwork()
    for host in (EXTERNAL_IP, LOOPBACK, LOCAL_NAME):
        d = net.connect(host, 443, via_proxy=False)
        assert d.allowed is False, host
        assert "只能经 harness 代理" in d.reason


def test_connect_via_proxy_succeeds() -> None:
    """走代理成功——再由代理做 scope 校验。"""
    net = SandboxNetwork()
    assert net.connect(EXTERNAL_IP, 443, via_proxy=True).allowed is True


def test_proxy_loopback_port_is_reachable_directly() -> None:
    net = SandboxNetwork()
    assert net.connect(LOOPBACK, net.proxy_port, via_proxy=False).allowed is True


def test_any_other_port_on_loopback_is_also_blocked() -> None:
    """只有代理端口，不是"回环全通"。"""
    net = SandboxNetwork()
    assert net.connect(LOOPBACK, 22, via_proxy=False).allowed is False


def test_attempts_are_recorded() -> None:
    net = SandboxNetwork()
    net.connect(EXTERNAL_IP, 80, via_proxy=False)
    net.connect(EXTERNAL_IP, 80, via_proxy=True)
    assert len(net.attempts) == 2
    assert {a["via_proxy"] for a in net.attempts} == {True, False}


# ══════════════════════════════════════════════════════════════════
# ③ 风险双判
# ══════════════════════════════════════════════════════════════════
def test_declared_risk_below_policy_is_rejected() -> None:
    """把重的说成轻的 = 逃避审批，不接受。"""
    script = 'import json\nopen("x.txt", "w").write("1")'
    with pytest.raises(GateRejected, match="风险双判"):
        check_risk(Risk.LOW, script)


def test_declared_risk_above_policy_is_allowed() -> None:
    """保守申报不该被惩罚。"""
    assert check_risk(Risk.HIGH, "print(1)") is Risk.LOW


def test_policy_risk_is_deterministic() -> None:
    script = "print(len([1,2]))"
    assert policy_risk(script) is policy_risk(script)


def test_network_script_is_high_risk() -> None:
    assert policy_risk("import requests") is Risk.HIGH


# ══════════════════════════════════════════════════════════════════
# ① schema
# ══════════════════════════════════════════════════════════════════
def test_oversized_script_is_rejected() -> None:
    with pytest.raises(GateRejected, match="schema"):
        run_shell(_req("x" * (MAX_SCRIPT_BYTES + 1)), policy=_policy(), executor=_exec_ok)


def test_empty_script_is_rejected() -> None:
    with pytest.raises(GateRejected, match="schema"):
        run_shell(_req("   "), policy=_policy(), executor=_exec_ok)


def test_bad_timeout_is_rejected() -> None:
    for t in (0, -1, 601):
        with pytest.raises(GateRejected, match="schema"):
            run_shell(_req("print(1)", timeout_s=t), policy=_policy(), executor=_exec_ok)


def test_absolute_cwd_is_rejected() -> None:
    with pytest.raises(GateRejected, match="schema"):
        run_shell(_req("print(1)", cwd=ABS_DIR), policy=_policy(), executor=_exec_ok)


def test_traversal_cwd_is_rejected() -> None:
    with pytest.raises(GateRejected, match="schema"):
        run_shell(_req("print(1)", cwd=".." + SEP + "x"), policy=_policy(), executor=_exec_ok)


# ══════════════════════════════════════════════════════════════════
# 05 §8.5 双维度
# ══════════════════════════════════════════════════════════════════
def test_read_only_policy_cannot_run_shell() -> None:
    with pytest.raises(GateRejected, match="沙箱"):
        run_shell(_req("print(1)"), policy=_policy(sandbox_mode=SandboxMode.READ_ONLY),
                  executor=_exec_ok)


def test_never_policy_cannot_run_shell() -> None:
    with pytest.raises(GateRejected, match="审批"):
        run_shell(_req("print(1)"),
                  policy=_policy(approval_policy=ApprovalPolicy.NEVER), executor=_exec_ok)


def test_high_risk_requires_always_approval() -> None:
    """策略判成 high 时，即使 on_risk 也不够——必须 always。"""
    script = "import requests" + chr(10) + "print(requests)"
    with pytest.raises(GateRejected, match="审批"):
        run_shell(_req(script, risk_declared=Risk.HIGH),
                  policy=_policy(approval_policy=ApprovalPolicy.ON_RISK),
                  executor=_exec_ok)


def test_explicit_user_approval_raises_requirement() -> None:
    with pytest.raises(GateRejected, match="审批"):
        run_shell(_req("print(1)", require_user_approval=True),
                  policy=_policy(approval_policy=ApprovalPolicy.ON_RISK), executor=_exec_ok)


def test_action_requirement_table_matches_doc() -> None:
    assert ACTION_REQUIREMENTS["fs.read"] == (SandboxMode.READ_ONLY, ApprovalPolicy.NEVER)
    assert ACTION_REQUIREMENTS["fs.write"] == (SandboxMode.WORKSPACE_WRITE,
                                               ApprovalPolicy.ON_RISK)
    assert ACTION_REQUIREMENTS["shell.run"] == (SandboxMode.WORKSPACE_WRITE,
                                                ApprovalPolicy.ON_RISK)
    assert ACTION_REQUIREMENTS["act.exec_tool"] == (SandboxMode.FULL, ApprovalPolicy.ALWAYS)


def test_two_dimensions_are_independent() -> None:
    """一维时"写文件"和"发写请求"混在同一档；两维能把它们分开。"""
    p = WorkbenchPolicy(SandboxMode.WORKSPACE_WRITE, ApprovalPolicy.ON_RISK)
    p.require(need_mode=SandboxMode.WORKSPACE_WRITE, need_approval=ApprovalPolicy.ON_RISK)
    with pytest.raises(GateRejected):
        p.require(need_mode=SandboxMode.FULL, need_approval=ApprovalPolicy.ALWAYS)


# ══════════════════════════════════════════════════════════════════
# ⑤ 产物归档 + 正常路径
# ══════════════════════════════════════════════════════════════════
def test_successful_run_archives_artifacts() -> None:
    r = run_shell(_req("print(1)"), policy=_policy(), executor=_exec_ok)
    assert r.exit_code == 0
    assert r.script_ref.startswith("out/workbench/")
    assert r.log_ref.endswith(".log")
    assert len(r.script_sha256) == 64
    assert r.risk_actual == "low"


def test_proxy_env_is_injected_and_script_needs_no_target() -> None:
    """脚本不需要知道目标地址：环境变量里只有代理。"""
    seen: dict[str, str] = {}

    def ex(script, interp, cwd, timeout, env):  # noqa: ANN001
        seen.update(env)
        return 0, "", ""

    run_shell(_req("print(1)"), policy=_policy(), executor=ex,
              env={"HTTP_PROXY": "SHOULD_BE_OVERRIDDEN", "SECRET": "x"})
    assert seen["HTTP_PROXY"].startswith(PROXY_PREFIX)
    assert "SECRET" not in seen  # 不在白名单里就不透传


def test_env_allowlist_only_passes_named_vars() -> None:
    seen: dict[str, str] = {}

    def ex(script, interp, cwd, timeout, env):  # noqa: ANN001
        seen.update(env)
        return 0, "", ""

    run_shell(_req("print(1)", env_allowlist=("HOME",)), policy=_policy(),
              executor=ex, env={"HOME": "work", "SECRET": "x"})
    assert seen["HOME"] == "work"
    assert "SECRET" not in seen


def test_script_hash_is_content_addressed() -> None:
    a = run_shell(_req("print(1)"), policy=_policy(), executor=_exec_ok)
    b = run_shell(_req("print(1)"), policy=_policy(), executor=_exec_ok)
    assert a.script_sha256 == b.script_sha256
    c = run_shell(_req("print(2)"), policy=_policy(), executor=_exec_ok)
    assert c.script_sha256 != a.script_sha256


def test_gates_are_described_for_ui() -> None:
    """用户要知道被哪一闸拦了。"""
    g = describe_gates()
    assert len(g) == 5
    assert all(v.strip() for v in g.values())


def test_rejected_gate_names_itself() -> None:
    with pytest.raises(GateRejected) as exc:
        run_shell(_req(f'x = "{BAD_URL}"'), policy=_policy(), executor=_exec_ok)
    assert "静态扫描" in exc.value.gate


def test_placeholders_are_declared() -> None:
    assert "PROXY_LOOPBACK" in ADDRESS_PLACEHOLDERS
