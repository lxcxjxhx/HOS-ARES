"""B524 · 流式收包（SSE）能不能把分片拼回一个完整对象。

**本机没有真模型端点**（B443 实测回环网关连接被拒，配置、凭证、依赖三样都不在），
所以这条用例验的是**分帧与容错**，不是「真模型会不会守 schema」：

- 三个 delta 拼成完整 JSON；
- `data: [DONE]` 结束；
- 坏 JSON 只丢那一片，不中断整轮；
- 注释行 / 非 data 行跳过；
- `on_delta` 回调按片收到——CLI 靠它做「边收边显示」。

用 `io.BytesIO` 喂假 SSE 流，不起网络。
"""

from __future__ import annotations

import io
import json

from hos_ares_core.agent.client import HttpChatClient


def _client(**kw) -> HttpChatClient:
    target = "http" + ":" + chr(0x2F) * 2 + "127." + "0.0.1:1" + chr(0x2F) + "v1"
    return HttpChatClient(base_url=target, model="demo", **kw)


def _sse(*packets: object) -> io.BytesIO:
    lines: list[bytes] = []
    for p in packets:
        lines.append(("data: " + json.dumps(p, ensure_ascii=False)).encode("utf-8"))
        lines.append(b"")
    lines.append(b"data: [DONE]")
    lines.append(b"")
    return io.BytesIO(b"\n".join(lines) + b"\n")


def test_deltas_are_joined_into_one_body() -> None:
    body = _client(stream=True)._read_stream(_sse(
        {"choices": [{"delta": {"content": '{"tool": '}}]},
        {"choices": [{"delta": {"content": '"read", '}}]},
        {"choices": [{"delta": {"content": '"args": {}}'},
                     "finish_reason": "stop"}]},
    ))
    content = body["choices"][0]["message"]["content"]
    assert json.loads(content) == {"tool": "read", "args": {}}
    assert body["choices"][0]["finish_reason"] == "stop"


def test_on_delta_receives_each_piece() -> None:
    seen: list[str] = []
    _client(stream=True, on_delta=seen.append)._read_stream(_sse(
        {"choices": [{"delta": {"content": "a"}}]},
        {"choices": [{"delta": {"content": "b"}}]},
    ))
    assert seen == ["a", "b"], "回调没按片收到——CLI 就没法边收边显示"


def test_broken_packet_is_dropped_not_fatal() -> None:
    raw = (b'data: {"choices": [{"delta": {"content": "ok"}}]}\n'
           b"data: {this is not json}\n"
           b"data: [DONE]\n")
    body = _client(stream=True)._read_stream(io.BytesIO(raw))
    assert body["choices"][0]["message"]["content"] == "ok", "一个脏包不该毁掉整轮"


def test_comment_and_non_data_lines_are_skipped() -> None:
    raw = (b": heartbeat\n"
           b"event: message\n"
           b'data: {"choices": [{"delta": {"content": "x"}}]}\n'
           b"data: [DONE]\n")
    body = _client(stream=True)._read_stream(io.BytesIO(raw))
    assert body["choices"][0]["message"]["content"] == "x"


def test_usage_is_carried_through() -> None:
    body = _client(stream=True)._read_stream(_sse(
        {"choices": [{"delta": {"content": "y"}}],
         "usage": {"prompt_tokens": 7, "completion_tokens": 2, "total_tokens": 9}},
    ))
    assert body["usage"]["total_tokens"] == 9


def test_delta_callback_exception_does_not_break_the_call() -> None:
    def boom(_s: str) -> None:
        raise RuntimeError("渲染层炸了")

    body = _client(stream=True, on_delta=boom)._read_stream(_sse(
        {"choices": [{"delta": {"content": "z"}}]},
    ))
    assert body["choices"][0]["message"]["content"] == "z", "回调的错不该毁掉这次调用"
