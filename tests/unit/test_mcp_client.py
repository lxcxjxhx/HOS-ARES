"""B522 · MCP 客户端（JSON-RPC over stdio）有没有真的能跑。

判据：拿一个**最小 MCP server**（下面 `_SERVER` 那几十行）跑完整三段——
`initialize` → `tools/list` → `tools/call`，再验两条失败路径
（服务端回 error、服务端提前退出）。三条一起过，才叫「这条链路是通的」。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from hos_ares_core.mcp import MCPClient, MCPError

#: 最小 MCP server：读一行、回一行。故意不实现完整规范——
#: 客户端只需要这三段，多写的部分反而会掩盖协议层的错。
_SERVER = r'''
import json, sys

def reply(obj):
    print(json.dumps(obj, ensure_ascii=False), flush=True)

for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    req = json.loads(line)
    method, rid = req.get("method"), req.get("id")
    if method == "notifications/initialized":
        continue
    if method == "initialize":
        reply({"jsonrpc": "2.0", "id": rid,
               "result": {"protocolVersion": "2024-11-05",
                          "capabilities": {"tools": {}},
                          "serverInfo": {"name": "demo-server", "version": "0.0.1"}}})
    elif method == "tools/list":
        reply({"jsonrpc": "2.0", "id": rid,
               "result": {"tools": [{"name": "echo", "description": "原样回显",
                                     "inputSchema": {"type": "object"}},
                                    {"name": "boom", "description": "总是失败",
                                     "inputSchema": {"type": "object"}}]}})
    elif method == "tools/call":
        name = req["params"]["name"]
        args = req["params"].get("arguments") or {}
        if name == "boom":
            reply({"jsonrpc": "2.0", "id": rid,
                   "result": {"isError": True,
                              "content": [{"type": "text", "text": "炸了"}]}})
        else:
            reply({"jsonrpc": "2.0", "id": rid,
                   "result": {"content": [{"type": "text",
                                           "text": "echo:" + json.dumps(args, ensure_ascii=False)}]}})
    else:
        reply({"jsonrpc": "2.0", "id": rid,
               "error": {"code": -32601, "message": "unknown method " + str(method)}})
'''

#: 立刻退出、什么都不回的 server——用来验「没回话」这条路径
_SILENT = "import sys; sys.exit(0)\n"


def _server(tmp_path: Path, body: str = _SERVER) -> list[str]:
    p = tmp_path / "mcp_server.py"
    p.write_text(body, encoding="utf-8")
    return [sys.executable, str(p)]


def test_full_handshake_and_tool_roundtrip(tmp_path: Path) -> None:
    with MCPClient(_server(tmp_path)) as c:
        res = c.initialize()
        assert res["protocolVersion"] == "2024-11-05"
        assert c.server_info.get("name") == "demo-server"

        tools = c.list_tools()
        assert [t.name for t in tools] == ["echo", "boom"]
        assert tools[0].description == "原样回显"
        assert tools[0].as_chat_tool()["input_schema"] == {"type": "object"}

        is_err, text = c.call_tool("echo", {"text": "你好"})
        assert is_err is False
        assert "你好" in text


def test_tool_reported_error_is_not_an_exception(tmp_path: Path) -> None:
    """工具自己说「我失败了」要能分辨——它不是链路故障。"""
    with MCPClient(_server(tmp_path)) as c:
        c.initialize()
        is_err, text = c.call_tool("boom", {})
        assert is_err is True
        assert "炸了" in text


def test_protocol_error_raises(tmp_path: Path) -> None:
    with MCPClient(_server(tmp_path)) as c:
        c.initialize()
        with pytest.raises(MCPError, match="unknown method"):
            c._rpc("nope/nothing")


def test_server_that_dies_raises_instead_of_hanging(tmp_path: Path) -> None:
    """server 立刻退出时，客户端必须**报错**而不是一直等。"""
    with MCPClient(_server(tmp_path, _SILENT)) as c:
        with pytest.raises(MCPError):
            c.initialize()


def test_client_without_context_manager_refuses_to_work() -> None:
    with pytest.raises(MCPError, match="没起"):
        MCPClient([sys.executable, "-c", "pass"]).initialize()
