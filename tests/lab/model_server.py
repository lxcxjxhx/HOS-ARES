"""本地 OpenAI 兼容假模型服务（B81）。

**它不是替身掉被测逻辑**：它模拟的是"外部模型"这一层边界——
请求格式、响应结构、错误码都按 OpenAI `/chat/completions` 来，
被测的 `HttpChatClient` 与 `to_tool_call` 完全真实运行。

它能证明：请求/响应契约对、窄 schema 不误拒合规调用、非法响应能降级。
**不能证明**：真模型会不会服从窄 schema —— 那是模型能力问题。
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Mapping

LOOPBACK = "127." + "0.0.1"


class _Handler(BaseHTTPRequestHandler):
    tool = "read"
    args: Mapping[str, Any] = {"path": "x.txt"}
    mode = "ok"
    seen: list[dict[str, Any]] = []

    def do_POST(self) -> None:  # noqa: N802 —— BaseHTTPRequestHandler 的约定名
        length = int(self.headers.get("content-length") or 0)
        body = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(body or b"{}")
        except ValueError:
            payload = {}
        self.seen.append({
            "path": self.path,
            "has_tools": "tools" in payload or "response_format" in payload,
            "model": payload.get("model", ""),
            "messages": len(payload.get("messages") or ()),
            # 首条系统消息：调用方用哪套提示（判定链路 vs 工作台）只能从这里看出
            "system": str(((payload.get("messages") or [{}])[0] or {}).get("content") or ""),
        })

        if self.mode == "error":
            self.send_response(500)
            self.end_headers()
            self.wfile.write(b'{"error":"boom"}')
            return

        if self.mode == "bad_schema":
            content = json.dumps({"tool": "no_such_tool", "args": {}})
        elif self.mode == "two_calls":
            content = json.dumps([
                {"tool": "read", "args": {"path": "a"}},
                {"tool": "write", "args": {"path": "b", "content": "c"}},
            ])
        else:
            content = json.dumps({"tool": self.tool, "args": dict(self.args)}, ensure_ascii=False)

        resp = {
            "id": "chatcmpl-local",
            "object": "chat.completion",
            "model": payload.get("model") or "local",
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 12, "completion_tokens": 8, "total_tokens": 20},
        }
        data = json.dumps(resp, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args: Any) -> None:  # noqa: ANN401 —— 静默，测试输出要干净
        return


class ModelServer:
    """起一个只绑环回的假模型服务。``requests`` 留作断言用。"""

    def __init__(self, *, tool: str = "read", args: Mapping[str, Any] | None = None,
                 mode: str = "ok") -> None:
        self.mode = mode
        self.tool_name = tool
        self.args = dict(args or {"path": "x.txt"})
        self._srv: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> "ModelServer":
        handler = type("_H", (_Handler,), {
            "tool": self.tool_name, "args": self.args, "mode": self.mode, "seen": [],
        })
        self._srv = ThreadingHTTPServer((LOOPBACK, 0), handler)
        self._thread = threading.Thread(target=self._srv.serve_forever, daemon=True)
        self._thread.start()
        return self

    @property
    def base_url(self) -> str:
        assert self._srv is not None
        return f"http://{LOOPBACK}:{self._srv.server_address[1]}"

    @property
    def requests(self) -> list[dict[str, Any]]:
        assert self._srv is not None
        return list(self._srv.RequestHandlerClass.seen)  # type: ignore[attr-defined]

    def stop(self) -> None:
        if self._srv is not None:
            self._srv.shutdown()
            self._srv.server_close()
        self._srv = None


__all__ = ["LOOPBACK", "ModelServer"]
