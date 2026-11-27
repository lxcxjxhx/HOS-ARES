"""B527 · chat 真能调通 MCP 工具（B523 只在 client 层验过）。

端到端三件事：配置起进程 → 工具登记进白名单 → `_exec_step` 把调用转给 MCP server 并把结果带回来。
用最小 server（与 `test_mcp_client` 同一段），不起网络。
"""

from __future__ import annotations

import json
import sys
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from hos_ares_cli import cmd_chat
from hos_ares_core.workbench.fs import Workbench

_SERVER = r'''
import json, sys
from collections.abc import Iterator
from typing import Any

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
               "result": {"protocolVersion": "2024-11-05", "capabilities": {},
                          "serverInfo": {"name": "demo"}}})
    elif method == "tools/list":
        reply({"jsonrpc": "2.0", "id": rid,
               "result": {"tools": [{"name": "echo", "description": "回显",
                                     "inputSchema": {"type": "object"}}]}})
    elif method == "tools/call":
        args = req["params"].get("arguments") or {}
        reply({"jsonrpc": "2.0", "id": rid,
               "result": {"content": [{"type": "text",
                                       "text": "echo:" + json.dumps(args, ensure_ascii=False)}]}})
    else:
        reply({"jsonrpc": "2.0", "id": rid,
               "error": {"code": -32601, "message": "unknown"}})
'''


@pytest.fixture()
def mcp_env(tmp_path: Path) -> Iterator[Any]:
    server = tmp_path / "server.py"
    server.write_text(_SERVER, encoding="utf-8")
    cfg = tmp_path / "mcp.json"
    cfg.write_text(json.dumps({"servers": {"demo": {"command": [sys.executable, str(server)]}}}),
                   encoding="utf-8")
    loaded = cmd_chat._load_mcp(str(cfg))
    try:
        yield cfg
    finally:
        for client, _tools in loaded:
            client.__exit__()
        cmd_chat._MCP.clear()


def test_tool_is_registered_and_whitelisted(mcp_env: Path) -> None:
    assert "mcp__demo__echo" in cmd_chat._MCP
    assert cmd_chat._is_chat_tool("mcp__demo__echo"), "MCP 工具没进白名单——模型给了也会被拒"


def test_exec_step_calls_the_mcp_server(mcp_env: Path, tmp_path: Path) -> None:
    step = SimpleNamespace(tool="mcp__demo__echo", args={"text": "你好"})
    ok, msg = cmd_chat._exec_step(step, wb=Workbench(tmp_path), root=tmp_path)
    assert ok, msg
    assert "你好" in msg, f"结果没带回来：{msg}"
    assert "mcp__demo__echo" in msg, "回显里应标明是哪个 MCP 工具"


def test_unknown_mcp_tool_is_refused(tmp_path: Path) -> None:
    """表里没有的 `mcp__` 名字要拒绝，而不是当内置工具放过去。"""
    step = SimpleNamespace(tool="mcp__nope__nothing", args={})
    ok, msg = cmd_chat._exec_step(step, wb=Workbench(tmp_path), root=tmp_path)
    assert ok is False
    assert "未接上" in msg


def test_missing_config_is_not_fatal(tmp_path: Path) -> None:
    """配置不存在只是没有外部工具——会话必须照常开。"""
    assert cmd_chat._load_mcp(str(tmp_path / "nope.json")) == []
    assert cmd_chat._load_mcp("") == []
