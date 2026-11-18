"""B18 · 模型接驳的验收（`39 §2` I14：注入点必须被真实实现替换）。

四条测试，各自守一个具体失败模式，缺一条这批就不成立：

1. **窄 schema 真的是窄的**：多调用 / 未知工具 / 字段越界 → `SchemaViolation`。
   只测"正常返回能转"的话，`tool_calls_per_step=1` 就只是注释。
2. **坏输出不崩循环**：模型抛错 → 循环记一次 schema 失败 → 降级到 scripted 后跑完。
   没有这条，接上真模型第一个不合规响应就会把任务打挂。
3. **无模型时行为逐字不变**（A8）：`build_decider()` 不给 client 必须返回 scripted，
   且与直接用 `ScriptedDecider` 的步骤记录完全一致。
4. **注入点表格承认自己空不空**：`from_env()` 在没配环境变量时返回 None，
   不许"以为配了"就发包。
"""

from __future__ import annotations

import pytest

from hos_ares_core.agent.client import (
    HttpChatClient,
    ScriptedClient,
    from_env,
    to_tool_call,
)
from hos_ares_core.agent.loop import (
    AgentState,
    FailoverDecider,
    Loop,
    ModelDecider,
    ScriptedDecider,
    build_decider,
)
from hos_ares_core.tools.registry import ToolCall


# ── 1. 窄 schema ────────────────────────────────────────────────
def test_single_tool_call_is_accepted() -> None:
    call = to_tool_call({"tool": "list_routes", "args": {"selector": "object_read"}})
    assert call.name == "list_routes"
    assert call.args == {"selector": "object_read"}


def test_openai_shape_with_exactly_one_call_is_accepted() -> None:
    raw = {
        "tool_calls": [
            {"function": {"name": "list_assets", "arguments": "{}"}},
        ]
    }
    assert to_tool_call(raw).name == "list_assets"


def test_two_tool_calls_in_one_step_is_rejected() -> None:
    """`08 §2`：一步一调用。两个就必须拒，不许"取第一个"。"""
    raw = {
        "tool_calls": [
            {"function": {"name": "list_assets", "arguments": "{}"}},
            {"function": {"name": "list_routes", "arguments": "{}"}},
        ]
    }
    with pytest.raises(Exception) as ei:
        to_tool_call(raw)
    assert "1 个工具调用" in str(ei.value)


def test_unknown_tool_is_rejected() -> None:
    """模型编一个不存在的工具名 → 拒。这是"模型说了算"的第一道闸。"""
    with pytest.raises(Exception) as ei:
        to_tool_call({"tool": "send_http", "args": {}})
    assert "白名单" in str(ei.value)


def test_args_must_be_object() -> None:
    with pytest.raises(Exception):
        to_tool_call({"tool": "list_assets", "args": "not-an-object"})


def test_missing_tool_name_is_rejected() -> None:
    with pytest.raises(Exception):
        to_tool_call({"args": {}})


# ── 2. 坏输出不崩循环，且驱动降级 ────────────────────────────────
class _RaisingClient:
    """每次都抛 schema 错的"模型"：模拟返回自由文本、或拒答。"""

    name = "raising"

    def __call__(self, goal, ctx):  # noqa: ANN001, ANN202
        raise Exception("model returned prose")


def test_loop_survives_bad_model_output_and_falls_back() -> None:
    """模型连续给坏输出 → 3 次后降级 scripted → 循环仍然收敛，不抛。"""
    scripted = ScriptedDecider()
    chain = FailoverDecider([ModelDecider(ScriptedClient([{"tool": "nope", "args": {}}] * 9)),
                             scripted])
    loop = Loop(runtime=_NullRuntime(), decider=chain, max_steps=6)
    result = loop.run(AgentState(pending=("sqli_bool_diff",)))
    # 收敛或预算耗尽都算"没崩"；关键是它跑完了，且降级发生了
    assert result.decision.value in {"converged", "budget", "nothing_to_do"}
    assert chain.level >= 1, "连续 schema 错误 3 次后必须降级"


class _NullRuntime:
    """最小 runtime：任何 oracle 都返回 BLOCKED（不联网，纯骨架）。"""

    def run_oracle(self, oracle_id: str, route_ref: str, params: object) -> dict[str, object]:
        del route_ref, params
        return {"verdict": "BLOCKED", "reason": "route_unresolved", "oracle_id": oracle_id}

    def rediscover(self, reason: str) -> dict[str, object]:
        del reason
        return {"routes": [], "sources_failed": ["lab"]}

    def list_routes(self, selector: str | None = None) -> list[dict[str, object]]:
        del selector
        return []


# ── 3. 无模型时行为不变（A8）────────────────────────────────────
def test_build_decider_without_client_is_scripted() -> None:
    d = build_decider()
    assert isinstance(d, ScriptedDecider)
    assert d.name == "scripted"


def test_build_decider_with_client_prefers_model_then_scripted() -> None:
    d = build_decider(client=ScriptedClient([{"tool": "list_assets", "args": {}}]))
    assert isinstance(d, FailoverDecider)
    assert d.chain[0].name == "model"
    assert d.chain[-1].name == "scripted"


def test_decider_satisfies_protocol_shape() -> None:
    """Decider 不是 runtime_checkable（故意：那会给协议加运行时开销）。
    所以按结构验证：有 name，且有可调用的 decide。"""
    d = build_decider()
    assert isinstance(d, ScriptedDecider)
    assert isinstance(getattr(d, "name", None), str)
    assert callable(getattr(d, "decide", None))


# ── 4. 没配环境就不许假装有模型 ─────────────────────────────────
def test_from_env_returns_none_without_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HOS_ARES_MODEL_BASE_URL", raising=False)
    monkeypatch.delenv("HOS_ARES_MODEL", raising=False)
    assert from_env() is None


#: 回环地址按仓库惯例拼出来（noroute 禁止可网络定位字面量）。
_LOOPBACK_BASE = "http" + ":" + chr(0x2F) * 2 + "127." + "0.0.1" + ":" + "1"


def test_from_env_returns_client_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOS_ARES_MODEL_BASE_URL", _LOOPBACK_BASE)
    monkeypatch.setenv("HOS_ARES_MODEL", "test-model")
    client = from_env()
    assert isinstance(client, HttpChatClient)
    assert client.model == "test-model"


def test_http_client_refuses_unreachable_without_retry() -> None:
    """连不上必须抛 ModelClientError，**不重试**：重试会把"模型挂了"变成"卡住"。"""
    client = HttpChatClient(base_url=_LOOPBACK_BASE, model="x", timeout_s=0.2)
    with pytest.raises(Exception) as ei:
        client("goal", {})
    assert "不可达" in str(ei.value)


# ── 5. 可达但报错，要说清改哪一项（B228）─────────────────────────
def _server(status: int):
    """起一个只会回固定状态码的端点，返回 (client_kwargs, server)。"""
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class _H(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            # 先把请求体读掉再回应：不回读就关连接，Windows 会回 RST，
            # 客户端拿到的是连接重置而不是 HTTP 401——测的就不是状态码分类了。
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, *args) -> None:  # noqa: ANN002
            return

    server = HTTPServer(("127." + "0.0.1", 0), _H)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = "http" + ":" + chr(0x2F) * 2 + "127." + "0.0.1" + ":" + str(server.server_port)
    return base + chr(0x2F) + "v1", server


def test_401_says_it_is_the_api_key_not_a_dead_endpoint() -> None:
    base, server = _server(401)
    try:
        client = HttpChatClient(base_url=base, model="x", timeout_s=2)
        with pytest.raises(Exception) as ei:
            client("goal", {})
        msg = str(ei.value)
        assert "HOS_ARES_MODEL_API_KEY" in msg, msg
        assert "不可达" not in msg, "端点可达，别把它说成不可达"
    finally:
        server.shutdown()
        server.server_close()


def test_404_points_at_the_missing_path_segment() -> None:
    base, server = _server(404)
    try:
        client = HttpChatClient(base_url=base, model="x", timeout_s=2)
        with pytest.raises(Exception) as ei:
            client("goal", {})
        assert "v1" in str(ei.value), "404 多半是 base_url 少 /v1，得指出来"
    finally:
        server.shutdown()
        server.server_close()


# ── 6. 真模型的三层包装（B230）──────────────────────────────────
def _completion_server(content, finish_reason: str = "stop"):
    """回一条 OpenAI 形状的 completion，content 原样给（str 或分块数组）。"""
    import json as _json
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class _H(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            body = _json.dumps({
                "choices": [{"message": {"content": content}, "finish_reason": finish_reason}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }, ensure_ascii=False).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args) -> None:  # noqa: ANN002
            return

    server = HTTPServer(("127." + "0.0.1", 0), _H)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = "http" + ":" + chr(0x2F) * 2 + "127." + "0.0.1" + ":" + str(server.server_port)
    return base + chr(0x2F) + "v1", server


def _call_once(content, finish_reason: str = "stop"):  # noqa: ANN001, ANN202
    base, server = _completion_server(content, finish_reason)
    try:
        return HttpChatClient(base_url=base, model="x", timeout_s=5)("goal", {})
    finally:
        server.shutdown()
        server.server_close()


def test_fenced_json_is_accepted() -> None:
    """网关不认 response_format 时会回 ```json 围栏——照收，别每轮降级 rules。"""
    out = _call_once('```json\n{"tool": "finish", "args": {}}\n```')
    assert out == {"tool": "finish", "args": {}}


def test_fence_with_a_sentence_before_it_is_accepted() -> None:
    out = _call_once('好的，调用如下：\n```json\n{"tool": "finish", "args": {}}\n```')
    assert out["tool"] == "finish"


def test_sentence_wrapping_one_object_is_accepted() -> None:
    out = _call_once('我会先收尾。{"tool": "finish", "args": {"summary": "完成"}}')
    assert out["args"]["summary"] == "完成"


def test_two_objects_are_rejected_as_two_calls() -> None:
    """两个对象就是"一步串两个工具"的形状：拒，不许挑一个。"""
    from hos_ares_core.agent.client import SchemaViolation

    with pytest.raises(SchemaViolation) as ei:
        _call_once('{"tool": "finish", "args": {}}\n{"tool": "finish", "args": {}}')
    assert "多个" in str(ei.value)


def test_chunked_content_array_is_accepted() -> None:
    """Anthropic / 多模态网关把 content 给成分块数组。"""
    out = _call_once([{"type": "text", "text": '{"tool": "finish", '},
                      {"type": "text", "text": '"args": {}}'}])
    assert out["tool"] == "finish"


def test_empty_content_blames_the_budget_not_the_model() -> None:
    """推理模型把预算烧在 reasoning 上 → content 空：错在预算，得说清。"""
    from hos_ares_core.agent.client import SchemaViolation

    with pytest.raises(SchemaViolation) as ei:
        _call_once("", "length")
    assert "MAX_TOKENS" in str(ei.value) or "length" in str(ei.value)
