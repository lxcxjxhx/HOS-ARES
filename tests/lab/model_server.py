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
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Mapping

LOOPBACK = "127." + "0.0.1"


class _Handler(BaseHTTPRequestHandler):
    tool = "read"
    args: Mapping[str, Any] = {"path": "x.txt"}
    mode = "ok"
    delay_s = 0.0
    seen: list[dict[str, Any]] = []

    def _chunk(self, payload: str) -> None:
        """按 HTTP/1.1 chunked 编码写一片（长度用十六进制 + CRLF 包围）。"""
        CRLF = chr(13) + chr(10)
        raw = payload.encode("utf-8")
        self.wfile.write(f"{len(raw):X}".encode("ascii") + CRLF.encode("ascii"))
        self.wfile.write(raw)
        self.wfile.write(CRLF.encode("ascii"))

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

        if self.mode == "gateway_timeout":
            # 504：真网关在模型超时后的典型返回（B554）。客户端必须**降级而不是空转**。
            self.send_response(504)
            self.end_headers()
            self.wfile.write(b'{"error":"upstream timeout"}')
            return

        if self.mode == "unauthorized":
            self.send_response(401)
            self.end_headers()
            self.wfile.write(b'{"error":"invalid api key"}')
            return

        if self.mode == "slow_headers":
            # 首包延迟（B554）：先睡再发 header。验"调用方有没有超时/放弃"。
            time.sleep(float(self.delay_s))
            self.send_response(200)
            data = json.dumps({"choices": [{"message": {"content": "late"}}]}).encode()
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return

        if self.mode == "stream_cut":
            # 断流（B554）：发两片就关连接，**没有 [DONE]**。
            # 真模型/网关在网络抖动时就是这样，客户端不许把它当成"一轮说完了"。
            self.send_response(200)
            self.send_header("content-type", "text/event-stream")
            self.send_header("transfer-encoding", "chunked")
            self.end_headers()
            chunk = json.dumps({
                "choices": [{"delta": {"content": "partial "}}],
            })
            self._chunk(f"data: {chunk}\n\n")
            self.wfile.flush()
            # 直接关：不发 [DONE]，也不发终止 chunk
            self.close_connection = True
            return

        if self.mode == "stream_usage":
            # usage 分片（B554）：OpenAI 流式的最后一帧常带 usage（stream_options）。
            # 客户端要能从**分片里**读出来，而不是只能读非流式响应体的 usage。
            self.send_response(200)
            self.send_header("content-type", "text/event-stream")
            self.send_header("transfer-encoding", "chunked")
            self.end_headers()
            # 正文必须是**合法工具调用 JSON**：产品要求 content 能解析成 {"tool","args"}。
            # 按字符切成分片，拼起来仍是完整 JSON —— 这正是真流式的形状。
            payload_text = json.dumps({"tool": self.tool, "args": dict(self.args)})
            step = max(1, len(payload_text) // 3)
            pieces = [payload_text[i:i + step] for i in range(0, len(payload_text), step)]
            for piece in pieces:
                frame = json.dumps({"choices": [{"delta": {"content": piece}}]})
                self._chunk("data: " + frame + chr(10) + chr(10))
                self.wfile.flush()
            usage = json.dumps({
                "choices": [],
                "usage": {"prompt_tokens": 11, "completion_tokens": 2, "total_tokens": 13},
            })
            self._chunk("data: " + usage + chr(10) + chr(10))
            self._chunk("data: [DONE]\n\n")
            self.wfile.flush()
            return

        if self.mode == "bad_schema":
            content = json.dumps({"tool": "no_such_tool", "args": {}})
        elif self.mode == "fenced":
            # 真网关最常见的形状：围栏 + 一句人话（B230）
            content = ("好的，这是调用：\n```json\n"
                       + json.dumps({"tool": self.tool, "args": dict(self.args)}, ensure_ascii=False)
                       + "\n```")
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
                 mode: str = "ok", delay_s: float = 0.0) -> None:
        self.mode = mode
        self.delay_s = delay_s
        self.tool_name = tool
        self.args = dict(args or {"path": "x.txt"})
        self._srv: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> "ModelServer":
        handler = type("_H", (_Handler,), {
            "tool": self.tool_name, "args": self.args, "mode": self.mode,
            "delay_s": self.delay_s, "seen": [],
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
