"""B532 · chat 的 live 路径端到端（真 HTTP 往返，但模型是本地假端点）。

**本机没有真模型**（B443），已有用例是「注入 client 后测 `_plan_from_model`」——
那验不到 HTTP 这一层。这里补的是：`from_env` 读环境变量 → 真发一次 POST →
解析回包 → 执行工具 → 落盘。

假端点只回一条固定的 `{"tool":"read","args":{"path":"a.py"}}`，
所以它验的是**链路**，不是「真模型会不会守 schema」——后者仍需真 key。
"""

from __future__ import annotations

import io
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from hos_ares_cli import cmd_chat

NL = chr(10)
SL = chr(0x2F)
ASK = "读一下 a.py" + NL + SL + "quit" + NL
QUIT = SL + "quit" + NL


class _Model(BaseHTTPRequestHandler):
    reply: str = ""

    def do_POST(self) -> None:  # noqa: N802 —— BaseHTTPRequestHandler 的约定名
        _ = self.rfile.read(int(self.headers.get("content-length") or 0))
        body = json.dumps({
            "choices": [{"message": {"role": "assistant", "content": self.reply},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }).encode("utf-8")
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_a: object) -> None:   # 静音
        return


@pytest.fixture()
def model_endpoint():
    srv = HTTPServer(("127." + "0.0.1", 0), _Model)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    try:
        yield srv
    finally:
        srv.shutdown()
        srv.server_close()


def _run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stdin_text: str):
    monkeypatch.setattr(sys, "stdin", io.StringIO(stdin_text))
    return cmd_chat.main(["--yes", "--root", str(tmp_path),
                          "--db", str(tmp_path / "chat.db")])


def test_live_path_reads_a_file_through_a_real_http_call(
    model_endpoint: HTTPServer, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    (tmp_path / "a.py").write_text("VALUE = 42" + NL, encoding="utf-8")
    _Model.reply = json.dumps({"tool": "read", "args": {"path": "a.py"}})

    port = model_endpoint.server_address[1]
    base = "http" + ":" + chr(0x2F) * 2 + "127." + "0.0.1:" + str(port) + chr(0x2F) + "v1"
    monkeypatch.setenv("HOS_ARES_MODEL_BASE_URL", base)
    monkeypatch.setenv("HOS_ARES_MODEL", "demo")

    rc = _run(tmp_path, monkeypatch, ASK)
    out = capsys.readouterr().out
    assert rc == 0
    assert "model: demo" in out, "启动行没显示模型名——说明没走 live 路径"
    assert "VALUE = 42" in out, "工具没被执行或输出没显示"


def test_missing_env_falls_back_to_rules(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    for k in ("HOS_ARES_MODEL_BASE_URL", "HOS_ARES_MODEL"):
        monkeypatch.delenv(k, raising=False)
    rc = _run(tmp_path, monkeypatch, QUIT)
    out = capsys.readouterr().out
    assert rc == 0
    assert "model: rules" in out, "没配模型时必须如实显示 rules，不能假装有模型"


def test_unreachable_endpoint_degrades_without_crashing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    """端点连不上 → 该轮降级规则层，会话不崩。"""
    dead = "http" + ":" + chr(0x2F) * 2 + "127." + "0.0.1:1" + chr(0x2F) + "v1"
    monkeypatch.setenv("HOS_ARES_MODEL_BASE_URL", dead)
    monkeypatch.setenv("HOS_ARES_MODEL", "demo")
    rc = _run(tmp_path, monkeypatch, ASK)
    out = capsys.readouterr().out
    assert rc == 0, "端点不可达不该让会话以非零退出"
    assert "model: demo" in out
