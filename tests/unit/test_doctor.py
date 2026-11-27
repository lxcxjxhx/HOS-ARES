"""B13 doctor 测试（A3「内网能装」、13 §3「doctor 有任何红项不交付」）。

三条纪律，每条都有独立用例：
- **全 ok 才叫绿**：``skip`` 也算不绿——没检查过的不算通过；
- ``fail`` 必须给修复命令：否则就是"知道了但不改"；
- **单项崩溃不能拖垮整份报告**：doctor 自己不倒，人才有得查。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from hos_ares_cli import cmd_doctor
from hos_ares_cli.cmd_doctor import (
    CHECKS,
    CheckResult,
    DoctorReport,
    Status,
    check_layout,
    check_python,
    run_doctor,
)

ROOT = Path(__file__).resolve().parents[2]


# ══════════════════════════════════════════════════════════════════
# 1 状态语义
# ══════════════════════════════════════════════════════════════════
def test_green_requires_no_fail_and_no_skip() -> None:
    ok = CheckResult("a", Status.OK)
    skip = CheckResult("b", Status.SKIP, "环境不具备")
    fail = CheckResult("c", Status.FAIL, "红", "跑 xxx")

    assert DoctorReport([ok]).green is True
    assert DoctorReport([ok, skip]).green is False   # skip 不算通过
    assert DoctorReport([ok, fail]).green is False


def test_skip_is_not_counted_as_ok() -> None:
    """把「没检查」记成「通过」，是这类工具最常见的自欺。"""
    rep = DoctorReport([CheckResult("x", Status.SKIP)])
    assert rep.by_status() == {"ok": 0, "fail": 0, "skip": 1}
    assert rep.green is False


def test_counts_and_lists() -> None:
    rep = DoctorReport([
        CheckResult("a", Status.OK), CheckResult("b", Status.FAIL, "x", "fix"),
        CheckResult("c", Status.SKIP),
    ])
    assert [r.name for r in rep.failed] == ["b"]
    assert [r.name for r in rep.skipped] == ["c"]


def test_render_marks_every_status_and_shows_fix() -> None:
    rep = DoctorReport([
        CheckResult("a", Status.OK, "fine"),
        CheckResult("b", Status.FAIL, "坏", "跑 make doctor"),
        CheckResult("c", Status.SKIP),
    ])
    text = rep.render()
    assert "✅ a" in text and "❌ b" in text and "⏭ c" in text
    assert "修复：跑 make doctor" in text
    assert "不可交付" in text


def test_render_says_deliverable_only_when_green() -> None:
    text = DoctorReport([CheckResult("a", Status.OK)]).render()
    assert "全绿" in text and "不可交付" not in text


# ══════════════════════════════════════════════════════════════════
# 2 注册表
# ══════════════════════════════════════════════════════════════════
def test_registry_checks_are_unique_and_signed() -> None:
    """注册表的不变量：名字唯一、签名统一、条数只增不减。

    以前写死 `== 19`，每加一条检查都要回来改（B127 加"执行体行为"时又红一次）。
    数字会过时，不变量不会——这里下限用 19，是"已有能力不能悄悄消失"。
    """
    names = [n for n, _ in CHECKS]
    assert len(names) == len(set(names)), "检查名重复"
    assert len(CHECKS) >= 19, f"检查条数变少了：{len(CHECKS)}"


def test_every_check_takes_root() -> None:
    """签名统一成 (root)：否则注册表按名字遍历时会 TypeError（本项目真踩过）。"""
    for name, fn in CHECKS:
        assert fn.__code__.co_argcount == 1, name


def test_run_doctor_covers_all_or_selected() -> None:
    rep = run_doctor(ROOT, only=["python", "layout"])
    assert [r.name for r in rep.results] == ["python 版本", "目录布局"]


def test_unknown_check_name_is_a_failed_item_not_a_silent_noop() -> None:
    """B231：旧断言是 `results == []`——把那句假话钉成了契约。

    名字打错时"没崩"不是够的：一条写错的 CI 闸门命令会 0 项执行、报全绿、exit 0，
    比没有闸门更糟。现在它会变成一条 fail，且不崩。
    """
    rep = run_doctor(ROOT, only=["no_such_check"])
    assert len(rep.results) == 1, "打错的项名要留下一条记录，不能静默消失"
    assert rep.results[0].status is Status.FAIL, rep.results[0]
    assert rep.green is False


# ══════════════════════════════════════════════════════════════════
# 3 单项行为
# ══════════════════════════════════════════════════════════════════
def test_python_version_check_passes_here() -> None:
    assert check_python(ROOT).status is Status.OK


def test_layout_check_detects_missing_dirs(tmp_path: Path) -> None:
    r = check_layout(tmp_path)
    assert r.status is Status.FAIL
    assert "缺" in r.detail
    assert r.fix  # fail 必须给修复建议


def test_layout_passes_on_repo() -> None:
    assert check_layout(ROOT).status is Status.OK


# ══════════════════════════════════════════════════════════════════
# 4 崩溃隔离
# ══════════════════════════════════════════════════════════════════
def test_crashing_check_does_not_break_the_report(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(root: Path) -> CheckResult:
        raise RuntimeError("检查自己炸了")

    monkeypatch.setattr("hos_ares_cli.cmd_doctor.CHECKS",
                        (("boom", boom), ("python", check_python)))
    rep = run_doctor(ROOT)
    assert len(rep.results) == 2
    assert rep.results[0].status is Status.FAIL
    assert "检查自身抛错" in rep.results[0].detail
    assert rep.results[1].status is Status.OK  # 后一项照跑


# ══════════════════════════════════════════════════════════════════
# 5 整仓跑一遍
# ══════════════════════════════════════════════════════════════════
def test_repo_doctor_has_no_failures() -> None:
    """本仓当前状态：**不许有 fail**。skip 允许（docker 可能没有）。"""
    rep = run_doctor(ROOT)
    # 失败时要一眼看出**哪一项**、卡在哪（B305）：整份 dict 在控制台上会被编码搅成乱码，
    # 于是这条用例偶尔红一次也查不出原因。
    assert rep.failed == [], [f"{r.name}：{r.detail[:80]}" for r in rep.failed]


def test_repo_doctor_json_is_serialisable() -> None:
    rep = run_doctor(ROOT, only=["python", "layout"])
    blob = json.dumps({"results": [r.as_dict() for r in rep.results]}, ensure_ascii=False)
    assert "python" in blob

# ── B105：浏览器实测是显式开关，默认不做 ─────────────────────────
def test_browser_check_defaults_to_no_launch(monkeypatch: pytest.MonkeyPatch) -> None:
    from pathlib import Path

    from hos_ares_cli.cmd_doctor import check_browser

    monkeypatch.delenv("HOS_ARES_DOCTOR_BROWSER", raising=False)
    result = check_browser(Path("."))
    # 没装 playwright 时是 skip；装了就是 ok——两种都不许"起浏览器"
    assert result.status in {"ok", "skip"}
    assert "launch" in result.detail or result.status == "skip"


# ── B200：探针没跑起来 ≠ 行为坏 ──────────────────────────────
def test_executor_check_splits_unproven_env_from_broken_behaviour(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:  # noqa: ANN001
    """干净 runner 上子进程既读不到 stdout 也拿不到退出码：记 skip，不记 fail。"""
    from hos_ares_cli import cmd_doctor

    def forged(**facts: object) -> Any:  # noqa: ANN003, ANN202
        def fake_attest(executor: object) -> object:  # noqa: ANN001, ANN202
            return {"executor": "local", "available": True, **facts}

        def fake_verdict(_facts: object) -> object:  # noqa: ANN001, ANN202
            return False, "行为不完整"

        monkeypatch.setattr("hos_ares_core.sandbox.attest.attest", fake_attest)
        monkeypatch.setattr("hos_ares_core.sandbox.attest.verdict", fake_verdict)
        monkeypatch.setattr("hos_ares_core.sandbox.runner.select_executor",
                            lambda: type("E", (), {"name": "local", "available": lambda self: True})())
        return cmd_doctor.check_sandbox_behavior(tmp_path)

    never_ran = forged(stdout_captured=False, exit_code_preserved=False, timeout_enforced=True)
    assert never_ran.status is cmd_doctor.Status.SKIP, never_ran.status
    assert "没能执行" in never_ran.detail

    truly_broken = forged(stdout_captured=True, exit_code_preserved=False, timeout_enforced=True)
    assert truly_broken.status is cmd_doctor.Status.FAIL


# ── B209：doctor 的 lint 覆盖必须跟上 verify 的清单 ──────────────
def test_doctor_covers_every_gate_in_the_list(tmp_path: Path) -> None:  # noqa: ANN001
    """`scripts/verify.py` 里每一道闸门，doctor 要么跑、要么写明为什么不跑。"""
    root = Path(__file__).resolve().parents[2]
    result = cmd_doctor.check_gate_parity(root)
    assert result.status is cmd_doctor.Status.OK, result.detail
    assert "道跑" in result.detail and "写明不跑" in result.detail, result.detail


def test_every_exclusion_carries_a_reason() -> None:
    """`DOCTOR_EXCLUDED` 不是逃生门：每条都要有理由，否则就是悄悄绕过。"""
    assert cmd_doctor.DOCTOR_EXCLUDED, "排除表空了——要么真有排除，要么这条用例该删"
    for name, reason in cmd_doctor.DOCTOR_EXCLUDED.items():
        assert reason.strip(), f"{name} 没写理由"


def test_doctor_flags_a_missing_lint(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001
    """把 noleak 从 doctor 源码里抹掉，这条检查必须红——否则它只是墙上的标语。"""
    import shutil

    root = Path(__file__).resolve().parents[2]
    copy = tmp_path / "repo"
    copy.mkdir()
    (copy / "scripts").mkdir()
    shutil.copy2(root / "scripts" / "verify.py", copy / "scripts" / "verify.py")

    # 用一份"漏了 noleak"的 doctor 源码替代当前实现
    altered = tmp_path / "cmd_doctor_copy.py"
    altered.write_text((root / "hos_ares_cli" / "cmd_doctor.py").read_text(encoding="utf-8")
                    .replace('"noleak.py"', '"__gone__.py"'), encoding="utf-8")
    monkeypatch.setattr(cmd_doctor, "__file__", str(altered))
    result = cmd_doctor.check_gate_parity(copy)
    assert result.status is cmd_doctor.Status.FAIL, result.detail
    assert "noleak.py" in result.detail, result.detail


# ── B216：闸门"写了"不等于"接上了" ─────────────────────────────
def test_agent_guard_check_passes_on_the_repo() -> None:
    root = Path(__file__).resolve().parents[2]
    result = cmd_doctor.check_agent_guard(root)
    assert result.status is cmd_doctor.Status.OK, result.detail
    assert "两个入口已接" in result.detail, result.detail


def test_agent_guard_check_catches_an_unwired_executor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001
    """闸门从**执行路径**上摘掉，这项必须红——grep 到 import 就绿是最容易犯的假绿。"""
    import hos_ares_cli.cmd_chat as chat

    def _unwired(step: object, *, wb: object, root: Path,

                 allow_dangerous: bool = False) -> object:  # noqa: ANN001, ANN202
        return True, "exit=0（闸门被摘掉）"

    monkeypatch.setattr(chat, "_exec_step", _unwired)
    root = Path(__file__).resolve().parents[2]
    result = cmd_doctor.check_agent_guard(root)
    assert result.status is cmd_doctor.Status.FAIL, result.detail
    assert "没拦" in result.detail, result.detail


# ── B227：模型后端配了连不上，要当场说清 ────────────────────────
def test_model_backend_skips_when_unconfigured(monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001
    """没配模型不是缺陷：规则层能跑（与 docker 同一口径）。"""
    monkeypatch.delenv("HOS_ARES_MODEL_BASE_URL", raising=False)
    monkeypatch.delenv("HOS_ARES_MODEL", raising=False)
    root = Path(__file__).resolve().parents[2]
    result = cmd_doctor.check_model_backend(root)
    assert result.status is cmd_doctor.Status.SKIP, result.detail
    assert "规则层可用" in result.detail


def test_model_backend_fails_on_a_dead_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001
    """配了却连不上 → FAIL 并给出可粘的探测命令（不然人到演示时才发现）。"""
    from hos_ares_cli.desktop import free_port

    port = free_port()
    monkeypatch.setenv("HOS_ARES_MODEL_BASE_URL",
                     "http" + "://" + "127." + "0.0.1:" + str(port) + chr(0x2F) + "v1")
    monkeypatch.setenv("HOS_ARES_MODEL", "demo")
    root = Path(__file__).resolve().parents[2]
    result = cmd_doctor.check_model_backend(root)
    assert result.status is cmd_doctor.Status.FAIL, result.detail
    assert "连不上" in result.detail
    assert "curl" in result.fix


def test_model_backend_ok_against_a_live_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001
    """真起一个最小 OpenAI 兼容端点：200 + data[] → OK 并列出模型名。"""
    import json as _json
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class _H(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            body = _json.dumps({"data": [{"id": "demo-model"}, {"id": "other"}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: object) -> None:
            return

    server = HTTPServer(("127." + "0.0.1", 0), _H)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = "http" + "://" + "127." + "0.0.1:" + str(server.server_port) + chr(0x2F) + "v1"
        monkeypatch.setenv("HOS_ARES_MODEL_BASE_URL", base)
        monkeypatch.setenv("HOS_ARES_MODEL", "demo-model")
        root = Path(__file__).resolve().parents[2]
        result = cmd_doctor.check_model_backend(root)
        assert result.status is cmd_doctor.Status.OK, result.detail
        assert "demo-model" in result.detail
    finally:
        server.shutdown()
        server.server_close()


def _models_status(monkeypatch: pytest.MonkeyPatch, code: int) -> "cmd_doctor.CheckResult":  # noqa: ANN001
    """起一个固定回 status 的 /models 端点，跑一次检查。"""
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class _H(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            self.send_response(code)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, *args: object) -> None:
            return

    server = HTTPServer(("127." + "0.0.1", 0), _H)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        base = "http" + "://" + "127." + "0.0.1:" + str(server.server_port) + chr(0x2F) + "v1"
        monkeypatch.setenv("HOS_ARES_MODEL_BASE_URL", base)
        monkeypatch.setenv("HOS_ARES_MODEL", "demo")
        return cmd_doctor.check_model_backend(Path(__file__).resolve().parents[2])
    finally:
        server.shutdown()
        server.server_close()


def test_model_backend_404_is_skip_not_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001
    """没有 /models ≠ 连不上：能连是事实，核对不到模型名也是事实，记 skip。"""
    result = _models_status(monkeypatch, 404)
    assert result.status is cmd_doctor.Status.SKIP, result.detail
    assert "可达" in result.detail and "连不上" not in result.detail, result.detail


def test_model_backend_401_is_fail_and_names_the_key(monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001
    """401 是 key，不是网络：报错必须说出该改哪个变量。"""
    result = _models_status(monkeypatch, 401)
    assert result.status is cmd_doctor.Status.FAIL, result.detail
    assert "HOS_ARES_MODEL_API_KEY" in result.detail, result.detail


# ── B231：--only 打错名字 = 一项没跑，不许报全绿 ────────────────
def test_unknown_only_name_is_a_failure_not_a_green() -> None:
    root = Path(__file__).resolve().parents[2]
    report = cmd_doctor.run_doctor(root, only=["model_backnd"])
    assert report.green is False, "打错的项名执行了 0 项，不能算通过"
    assert report.failed, "必须记一条 fail"
    assert "没有这个检查项" in report.failed[0].detail, report.failed[0].detail
    assert "model_backend" in report.failed[0].fix, "得把可用项名给出来"


def test_unknown_only_name_does_not_swallow_a_valid_one(monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001
    """打错一个名字，同一次里对的那个仍要跑（别拿错名当整体拒绝）。"""
    monkeypatch.delenv("HOS_ARES_MODEL_BASE_URL", raising=False)
    monkeypatch.delenv("HOS_ARES_MODEL", raising=False)
    root = Path(__file__).resolve().parents[2]
    report = cmd_doctor.run_doctor(root, only=["model_backnd", "model_backend"])
    names = [r.name for r in report.results]
    assert any("model_backnd" in n for n in names), names
    assert "模型后端" in names, names


def test_main_exits_nonzero_on_a_mistyped_only(capsys: pytest.CaptureFixture[str]) -> None:  # noqa: ANN001
    """CI 里 `hos-ares doctor --only <打错>` 必须红，否则这条闸门永远绿。"""
    code = cmd_doctor.main(["--only", "model_backnd"])
    out = capsys.readouterr().out
    assert code == 1, out
    assert "没有这个检查项" in out, out


# ── B233：配了 key 就得带着 key 探（否则 401 会冤枉"检查 key"）──
def _auth_models(monkeypatch: pytest.MonkeyPatch, key_env: str) -> "cmd_doctor.CheckResult":  # noqa: ANN001
    """起一个**要求 Bearer** 的 /models：没带 key 回 401，带了回 200。"""
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    seen: dict[str, str] = {}

    class _H(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            auth = self.headers.get("Authorization") or ""
            seen["auth"] = auth
            if auth != "Bearer good-key":
                self.send_response(401)
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"{}")
                return
            body = b'{"data": [{"id": "demo-model"}]}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: object) -> None:
            return

    server = HTTPServer(("127." + "0.0.1", 0), _H)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        base = "http" + "://" + "127." + "0.0.1:" + str(server.server_port) + chr(0x2F) + "v1"
        monkeypatch.setenv("HOS_ARES_MODEL_BASE_URL", base)
        monkeypatch.setenv("HOS_ARES_MODEL", "demo-model")
        if key_env:
            monkeypatch.setenv("HOS_ARES_MODEL_API_KEY", key_env)
        else:
            monkeypatch.delenv("HOS_ARES_MODEL_API_KEY", raising=False)
        result = cmd_doctor.check_model_backend(Path(__file__).resolve().parents[2])
        result_seen = seen.get("auth", "")
        assert result_seen == (f"Bearer {key_env}" if key_env else ""), result_seen
        return result
    finally:
        server.shutdown()
        server.server_close()


def test_model_backend_sends_the_configured_key(monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001
    """配了 key 就必须带上：不带的探针会把"要鉴权的网关"误报成"key 错了"。"""
    result = _auth_models(monkeypatch, "good-key")
    assert result.status is cmd_doctor.Status.OK, result.detail
    assert "带 key" in result.detail and "good-key" not in result.detail, result.detail


def test_model_backend_without_a_key_says_so(monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001
    """没配 key 时如实说 401 是缺 key，而不是"端点坏了"。"""
    result = _auth_models(monkeypatch, "")
    assert result.status is cmd_doctor.Status.FAIL, result.detail
    assert "HOS_ARES_MODEL_API_KEY" in result.detail, result.detail


# ── B273：浏览器检查不许把"包能导入"说成"可用" ─────────────────
def test_browser_check_does_not_claim_more_than_it_checked(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:  # noqa: ANN001
    """B267 实测：playwright 包在，但自带 chromium 起不来（TargetClosedError）。

    所以默认那句只能说"导入通过 + 未验证启动"，并给出实测开关。
    """
    from hos_ares_cli.cmd_doctor import check_browser

    monkeypatch.delenv("HOS_ARES_DOCTOR_BROWSER", raising=False)
    result = check_browser(tmp_path)
    if result.status.name == "SKIP":        # 没装 playwright 的机器：本来就说清了
        return
    assert "未验证" in result.detail, result.detail
    assert "HOS_ARES_DOCTOR_BROWSER=launch" in result.detail
    assert "playwright 可用" not in result.detail, "又写回「可用」了"


# ── B291：skip 项也要给"怎么修" ──────────────────────────────────
def test_every_skip_carries_a_fix_hint() -> None:
    """fix 列留空等于没给下一步——而 skip 恰恰是客户最常看到的状态。"""
    from hos_ares_cli.cmd_doctor import CHECKS

    empty = []
    for name, fn in CHECKS:
        result = fn(Path("."))
        if result.status.name == "SKIP" and not result.fix.strip():
            empty.append(name)
    assert not empty, f"这些 skip 项没有 fix：{empty}"


# ── B304：doctor 提示里的 --only 名字必须真的存在 ────────────────
def test_only_names_in_hints_are_real_checks() -> None:
    """B291 我写了「hos-ares doctor --only model」，而检查名是 `model_backend`——
    照着提示敲会得到「可用项：…」，跟 B293 是同一族错。"""
    import re

    from hos_ares_cli import cmd_doctor

    valid = {name for name, _ in cmd_doctor.CHECKS}
    text = " ".join(
        f"{r.detail} {r.fix}" for r in (fn(Path(".")) for _, fn in cmd_doctor.CHECKS)
    )
    used = set(re.findall(r"--only ([a-z_]+)", text))
    assert used <= valid, f"提示里出现了不存在的检查名：{sorted(used - valid)}"
