"""B81 · chat 接真模型路径（H23 判据 A–E）。

用本地 OpenAI 兼容假服务验证**链路正确性**：请求格式、响应解析、
窄 schema 不误拒、非法响应能降级。**不能**证明真模型会不会服从窄 schema。
"""

from __future__ import annotations

import io
import re
from pathlib import Path
from typing import Any

import pytest

SEP = chr(0x2F)
ANSI = re.compile(chr(27) + r"\[[0-9;]*m")


@pytest.fixture()
def model():  # noqa: ANN201
    from lab.model_server import ModelServer

    srv = ModelServer(tool="read", args={"path": "notes.txt"}, mode="ok").start()
    try:
        yield srv
    finally:
        srv.stop()


@pytest.fixture()
def ctx(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):  # noqa: ANN201
    def _chat(script: str, *, base_url: str = "", model_name: str = "local-model") -> str:
        from veripen_cli.cmd_chat import main

        monkeypatch.setenv("VERIPEN_MODEL_BASE_URL", base_url)
        monkeypatch.setenv("VERIPEN_MODEL", model_name)
        monkeypatch.setattr("sys.stdin", io.StringIO(script))
        main(["--root", str(tmp_path), "--db", str(tmp_path / "c.db"), "--yes"])
        return ""

    return _chat


def _out(capsys: pytest.CaptureFixture[str]) -> str:
    return ANSI.sub("", capsys.readouterr().out)


# ── A：模型调用被采纳 ──────────────────────────────────────────
def test_model_plan_is_used(model: Any, ctx: Any, tmp_path: Path,
                            capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / "notes.txt").write_text("hi\n", encoding="utf-8")
    ctx("随便说点什么\n" + SEP + "quit\n", base_url=model.base_url)
    out = _out(capsys)

    assert "模型：read" in out, f"没走模型路径：{out[:300]}"
    assert "✓" in out and "notes.txt" in out, "模型的调用没被执行"
    assert model.requests, "假服务没收到请求"


# ── E：请求真的是 OpenAI 形状 ─────────────────────────────────
def test_request_shape(model: Any, ctx: Any,
                       capsys: pytest.CaptureFixture[str]) -> None:
    ctx("看下 notes.txt\n" + SEP + "quit\n", base_url=model.base_url)
    _out(capsys)
    req = model.requests[0]
    want = SEP + "chat" + SEP + "completions"
    assert req["path"].endswith(want), f"路径不对：{req['path']}"
    assert req["has_tools"], "没有带 tools / response_format"
    assert req["model"] == "local-model"


# ── D：横幅如实渲染 ────────────────────────────────────────────
def test_banner_shows_model_name(model: Any, ctx: Any,
                                 capsys: pytest.CaptureFixture[str]) -> None:
    ctx(SEP + "quit\n", base_url=model.base_url)
    out = _out(capsys)
    assert "model: local-model" in out


# ── B：非法响应降级 ────────────────────────────────────────────
def test_bad_schema_falls_back(monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
                               ctx: Any, capsys: pytest.CaptureFixture[str]) -> None:
    from lab.model_server import ModelServer

    srv = ModelServer(mode="bad_schema").start()
    try:
        (tmp_path / "notes.txt").write_text("hi\n", encoding="utf-8")
        ctx("看下 notes.txt\n" + SEP + "quit\n", base_url=srv.base_url)
        out = _out(capsys)
        assert "降级 rules" in out, f"非法响应没有降级提示：{out[:300]}"
        # 降级后 rules 仍能把这句话理解成 read
        assert "read" in out
    finally:
        srv.stop()


def test_two_calls_rejected(monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
                            ctx: Any, capsys: pytest.CaptureFixture[str]) -> None:
    from lab.model_server import ModelServer

    srv = ModelServer(mode="two_calls").start()
    try:
        ctx("看下 notes.txt\n" + SEP + "quit\n", base_url=srv.base_url)
        out = _out(capsys)
        assert "降级 rules" in out, "多调用必须被拒（一步一调用）"
    finally:
        srv.stop()


def test_transport_error_falls_back(monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
                                    ctx: Any, capsys: pytest.CaptureFixture[str]) -> None:
    from lab.model_server import ModelServer

    srv = ModelServer(mode="error").start()
    try:
        ctx("看下 notes.txt\n" + SEP + "quit\n", base_url=srv.base_url)
        out = _out(capsys)
        assert "降级 rules" in out, "500 也要降级而不是崩"
    finally:
        srv.stop()


# ── C：未配置时仍是 rules ─────────────────────────────────────
def test_no_model_uses_rules(ctx: Any, capsys: pytest.CaptureFixture[str]) -> None:
    ctx("状态\n" + SEP + "quit\n", base_url="")
    out = _out(capsys)
    assert "model: rules" in out
    assert "降级" not in out, "没配模型不该出现降级提示"
