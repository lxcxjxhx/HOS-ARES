#!/usr/bin/env python3
# =============================================================================
# HOS-ARES · mcp-compat-gw.py —— mobile-security-mcp 兼容适配网关 v3（零 SDK 依赖）
#
# 背景（实测结论，见 方案/…/12-Phase5-前置：工具调用级兼容探测.md）：
#   mobile-security-mcp 0.1.4 的 handler 为「单参整请求」签名（call_tool(request)），
#   mcp-SDK 各时代（0.9.1/1.9.3/1.28.1）均以 (name, arguments) 双参派发 → 恒报
#   "takes 1 positional argument but 2 were given"；tools/list 在 0.x/1.9.x 亦校验失败。
#
# 方案（可插拔 Agent 适配层）：
#   v3 完全绕过 mcp-SDK 的 Server/装饰器机制：
#     * tools/list  ← import mobile_security_mcp.server.TOOL_SCHEMAS（54 项纯 dict）
#     * tools/call  ← 按名称直调 handler 实例的对应方法（dispatch 表与 server.py 一致），
#                     支持 sync/async；check_tools / install_tool 直调 setup 模块函数。
#   对 reasonix（现代 MCP 客户端）零改动即可消费。stdio JSON-RPC 逐行帧，
#   遵守规范次序：initialize → notifications/initialized → tools/list → tools/call。
#
# 用法：
#   PYTHONPATH=<含 mcp==1.28.1 与 mobile-security-mcp 的目录> \
#   python /path/to/mcp-compat-gw.py
# =============================================================================
import asyncio
import inspect
import json
import sys

from mobile_security_mcp.handlers.device import DeviceHandler
from mobile_security_mcp.handlers.dynamic import DynamicHandler
from mobile_security_mcp.handlers.flutter import FlutterHandler
from mobile_security_mcp.handlers.memory import MemoryHandler
from mobile_security_mcp.handlers.native import NativeHandler
from mobile_security_mcp.handlers.network import NetworkHandler
from mobile_security_mcp.handlers.patcher import PatcherHandler
from mobile_security_mcp.handlers.rasp import RaspHandler
from mobile_security_mcp.handlers.signing import SigningHandler
from mobile_security_mcp.handlers.static import StaticHandler
from mobile_security_mcp.server import TOOL_SCHEMAS
from mobile_security_mcp.setup.check_tools import check_all_tools
from mobile_security_mcp.setup.install_tool import install_tool as _install_tool

HANDLERS = (
    StaticHandler(), DynamicHandler(), RaspHandler(), DeviceHandler(),
    NetworkHandler(), SigningHandler(), MemoryHandler(), NativeHandler(),
    FlutterHandler(), PatcherHandler(),
)


def _lookup_tool(name: str):
    """返回可调用 (args)->dict；找不到返回 None。dispatch 与 server.py 一致。"""
    for h in HANDLERS:
        fn = getattr(h, name, None)
        if fn is not None:
            return fn
    if name == "check_tools":
        return lambda _args: check_all_tools()
    if name == "install_tool":
        return lambda args: _install_tool((args or {}).get("tool_name", ""))
    return None


async def handle_call(msg: dict) -> dict:
    params = msg.get("params") or {}
    name = params.get("name", "")
    args = params.get("arguments") or {}
    fn = _lookup_tool(name)
    if fn is None:
        return {
            "content": [{"type": "text", "text": json.dumps(
                {"status": "error", "tool": name, "error": f"Unknown tool: {name}"},
                ensure_ascii=False)}],
            "isError": True,
        }
    try:
        if inspect.iscoroutinefunction(fn):
            result = await fn(args)
        elif inspect.isawaitable(fn(args)):
            result = await fn(args)
        else:
            result = fn(args)
    except Exception as exc:  # noqa: BLE001
        result = {"status": "error", "tool": name, "error": str(exc)}
    return {
        "content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}],
        "isError": bool(result.get("status") in ("error", "missing_tool", "missing")),
    }


async def serve_stdio() -> None:
    loop = asyncio.get_running_loop()

    def readline() -> str:
        return sys.stdin.readline()

    while True:
        raw = await loop.run_in_executor(None, readline)
        if not raw:
            break
        line = raw.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except Exception:
            continue
        rid = msg.get("id")
        method = msg.get("method", "")
        if rid is None:
            continue  # notification
        try:
            if method == "initialize":
                reply = {
                    "jsonrpc": "2.0", "id": rid,
                    "result": {
                        "protocolVersion": "2025-03-26",
                        "capabilities": {"tools": {"listChanged": False}},
                        "serverInfo": {
                            "name": "hos-ares-mobile-security-gw",
                            "version": "3.0.0",
                        },
                    },
                }
            elif method == "tools/list":
                reply = {"jsonrpc": "2.0", "id": rid, "result": {"tools": TOOL_SCHEMAS}}
            elif method == "tools/call":
                reply = {"jsonrpc": "2.0", "id": rid, "result": await handle_call(msg)}
            elif method == "ping":
                reply = {"jsonrpc": "2.0", "id": rid, "result": {}}
            else:
                reply = {"jsonrpc": "2.0", "id": rid,
                         "error": {"code": -32601, "message": "unsupported method: " + method}}
        except Exception as exc:  # noqa: BLE001
            reply = {"jsonrpc": "2.0", "id": rid,
                     "error": {"code": -32603, "message": "server error: " + str(exc)}}
        sys.stdout.write(json.dumps(reply, ensure_ascii=False) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    asyncio.run(serve_stdio())