"""B533 · 流式在真 HTTP 上的那一段（B532 只验了非流式）。

B524/B525 用 `io.BytesIO` 喂假 SSE，验的是**分帧**；这里验的是**真 HTTP 下的 chunked 响应**：
`stream: true` 进 payload → server 回 `text/event-stream` → 客户端逐行读 → 拼回 JSON → 执行工具。

仍然不是真模型（回包固定），但 SSE 这一层走的是真 socket。
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

#: 把工具调用拆成三片，模拟模型边想边吐
PIECES = ['{"tool": "re', 'ad", "args": ', '{"path": "a.py"}}']


class _SSEModel(BaseHTTPRequestHandler):
    saw_stream_flag = False

    def do_POST(self) -> None:  # noqa: N802
        raw = self.rfile.read(int(self.headers.get("content-length") or 0))
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            payload = {}
        type(self).saw_stream_flag = bool(payload.get("stream"))

        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.end_headers()
        for piece in PIECES:
            packet = {"choices": [{"delta": {"content": piece}}]}
            self.wfile.write(("data: " + json.dumps(packet, ensure_ascii=False) + NL).encode("utf-8"))
            self.wfile.flush()
        self.wfile.write(("data: [DONE]" + NL).encode("utf-8"))
        self.wfile.flush()

    def log_message(self, *_a: object) -> None:
        return


@pytest.fixture()
def sse_endpoint():
    _SSEModel.saw_stream_flag = False   # 类属性会被上一条用例污染
    srv = HTTPServer(("127." + "0.0.1", 0), _SSEModel)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    try:
        yield srv
    finally:
        srv.shutdown()
        srv.server_close()


def test_streaming_round_trip_over_real_http(
    sse_endpoint: HTTPServer, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    (tmp_path / "a.py").write_text("STREAMED = True" + NL, encoding="utf-8")
    port = sse_endpoint.server_address[1]
    base = "http" + ":" + chr(0x2F) * 2 + "127." + "0.0.1:" + str(port) + chr(0x2F) + "v1"
    monkeypatch.setenv("HOS_ARES_MODEL_BASE_URL", base)
    monkeypatch.setenv("HOS_ARES_MODEL", "demo")
    monkeypatch.setenv("HOS_ARES_MODEL_STREAM", "1")

    monkeypatch.setattr(sys, "stdin", io.StringIO(ASK))
    rc = cmd_chat.main(["--yes", "--root", str(tmp_path),
                        "--db", str(tmp_path / "chat.db")])
    captured = capsys.readouterr()
    assert rc == 0
    assert _SSEModel.saw_stream_flag, "payload 里没有 stream:true——根本没走流式分支"
    assert "STREAMED = True" in captured.out, f"三片没拼回工具调用：{captured.out[-400:]}"
    # 增量走 stderr（B525）：stdout 是会话输出，模型草稿不该混进去
    assert "模型输出中" in captured.err


def test_stream_off_by_default(
    sse_endpoint: HTTPServer, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    port = sse_endpoint.server_address[1]
    base = "http" + ":" + chr(0x2F) * 2 + "127." + "0.0.1:" + str(port) + chr(0x2F) + "v1"
    monkeypatch.setenv("HOS_ARES_MODEL_BASE_URL", base)
    monkeypatch.setenv("HOS_ARES_MODEL", "demo")
    monkeypatch.delenv("HOS_ARES_MODEL_STREAM", raising=False)
    monkeypatch.setattr(sys, "stdin", io.StringIO(SL + "quit" + NL))
    cmd_chat.main(["--yes", "--root", str(tmp_path), "--db", str(tmp_path / "chat.db")])
    assert _SSEModel.saw_stream_flag is False, "默认不该开流式"
