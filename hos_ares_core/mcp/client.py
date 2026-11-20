"""MCP 客户端：JSON-RPC over stdio（B522）。

**为什么需要**：codex 的工具不只内置——它接 MCP server。HOS-ARES 之前只有内置工具
（`read/write/edit/patch/sh/status/run/platform`），接不了外部能力。这个模块补的就是那一层。

协议最小集（够用就好，不实现完整规范）：
- `initialize` → `{protocolVersion, capabilities, serverInfo}`
- `tools/list` → `{tools: [{name, description, inputSchema}]}`
- `tools/call` → `{content: [{type, text}], isError?}`

**纪律**：`call_tool` 的返回是**不可信文本**——它是外部进程说的话，和网页内容同级。
调用方负责把它当数据用，不要当指令执行。

用法：
    with MCPClient([sys.executable, "server.py"]) as c:
        c.initialize()
        for t in c.list_tools():
            print(t.name, t.description)
        print(c.call_tool("echo", {"text": "hi"}))
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
from dataclasses import dataclass
from typing import Any, Sequence

#: 协议版本：客户端说自己的，服务端可以回别的（回什么听什么，不强制）
PROTOCOL_VERSION = "2024-11-05"


class MCPError(RuntimeError):
    """MCP 通信或协议层失败。**不吞**——调用方要能区分「工具说不」与「链路断了」。"""


@dataclass(frozen=True)
class MCPTool:
    name: str
    description: str
    input_schema: dict[str, Any]

    def as_chat_tool(self) -> dict[str, Any]:
        """给模型看的形态（与内置工具同一套字段名）。"""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }


class MCPClient:
    """一个 MCP server 的生命周期 + 收发。

    线程安全：`_lock` 保护「写一行 + 读一行」这个配对——并发调用会把响应串到别人的请求上，
    所以宁可串行。
    """

    def __init__(self, cmd: Sequence[str], *, timeout_s: float = 20.0) -> None:
        if not cmd:
            raise MCPError("MCP server 命令为空")
        self._cmd = list(cmd)
        self._timeout_s = timeout_s
        self._proc: subprocess.Popen[str] | None = None
        self._lock = threading.Lock()
        self._next_id = 0
        self.server_info: dict[str, Any] = {}
        self.protocol_version = ""

    # ── 生命周期 ────────────────────────────────────────────────
    def __enter__(self) -> "MCPClient":
        try:
            self._proc = subprocess.Popen(  # noqa: S603 - 命令由调用方给出（配置文件）
                self._cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,          # 行缓冲：JSON-RPC 按行分帧
                # B523：子进程默认按本机编码写（Windows 上是 cp936），中文工具描述会变乱码。
                # 这与 B365 那条教训同源——跨进程管道必须两头都钉 utf-8。
                env={**os.environ, "PYTHONIOENCODING": "utf-8",
                     "PYTHONUNBUFFERED": "1"},
            )
        except OSError as exc:
            raise MCPError(f"起不来 MCP server：{exc}") from exc
        return self

    def __exit__(self, *_exc: object) -> None:
        proc = self._proc
        self._proc = None
        if proc is None:
            return
        try:
            if proc.stdin is not None:
                proc.stdin.close()
        except OSError:
            pass
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            proc.kill()

    # ── 收发 ────────────────────────────────────────────────────
    def _rpc(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        proc = self._proc
        if proc is None or proc.stdin is None or proc.stdout is None:
            raise MCPError("MCP server 没起：先用 `with MCPClient(...)`")
        with self._lock:
            self._next_id += 1
            req = {"jsonrpc": "2.0", "id": self._next_id, "method": method}
            if params is not None:
                req["params"] = params
            try:
                proc.stdin.write(json.dumps(req, ensure_ascii=False) + "\n")
                proc.stdin.flush()
            except (OSError, ValueError) as exc:
                raise MCPError(f"写不进 MCP server：{exc}") from exc

            # B528：**真超时**。`readline()` 是阻塞的——server 不回话时会一直等，
            # 而 `timeout_s` 之前只是个没生效的数字。Windows 上 select 不支持管道，
            # 所以用「一个短命线程 + join(timeout)」：读完即弃，不会有队列串味。
            box: list[str] = []
            out_pipe = proc.stdout   # 上面已确认非 None；绑定局部量给闭包与 mypy 看
            reader = threading.Thread(target=lambda: box.append(out_pipe.readline()),
                                      daemon=True)
            reader.start()
            reader.join(self._timeout_s)
            if not box:
                raise MCPError(f"MCP 超时：{self._timeout_s}s 内没有响应（{method}）")
            line = box[0]
            if not line:
                err = ""
                if proc.stderr is not None:
                    try:
                        err = (proc.stderr.read() or "")[-400:]
                    except OSError:
                        err = ""
                raise MCPError(f"MCP server 没回话（已退出？）{err}")
            try:
                msg = json.loads(line)
            except json.JSONDecodeError as exc:
                raise MCPError(f"MCP 回了非 JSON：{line[:200]!r}") from exc

            if "error" in msg:
                e = msg["error"]
                raise MCPError(f"{method} 失败：{e.get('code')} {e.get('message')}")
            result = msg.get("result")
            return result if isinstance(result, dict) else {"value": result}

    # ── 协议方法 ────────────────────────────────────────────────
    def initialize(self) -> dict[str, Any]:
        res = self._rpc("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "hos-ares", "version": "0.1.0"},
        })
        self.protocol_version = str(res.get("protocolVersion") or "")
        info = res.get("serverInfo")
        self.server_info = info if isinstance(info, dict) else {}
        # 规范要求初始化完成后发一条通知；不发也能用，但发了更合规
        try:
            self._notify("notifications/initialized")
        except MCPError:
            pass
        return res

    def _notify(self, method: str) -> None:
        proc = self._proc
        if proc is None or proc.stdin is None:
            raise MCPError("MCP server 没起")
        proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": method}) + "\n")
        proc.stdin.flush()

    def list_tools(self) -> list[MCPTool]:
        res = self._rpc("tools/list", {})
        raw = res.get("tools")
        out: list[MCPTool] = []
        for item in raw or []:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "")
            if not name:
                continue
            schema = item.get("inputSchema")
            out.append(MCPTool(
                name=name,
                description=str(item.get("description") or ""),
                input_schema=schema if isinstance(schema, dict) else {"type": "object"},
            ))
        return out

    def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> tuple[bool, str]:
        """返回 `(is_error, text)`。**text 是不可信数据**——外部进程说的话。"""
        res = self._rpc("tools/call", {"name": name, "arguments": arguments or {}})
        is_error = bool(res.get("isError"))
        chunks: list[str] = []
        for c in res.get("content") or []:
            if isinstance(c, dict) and c.get("type") == "text":
                chunks.append(str(c.get("text") or ""))
        if not chunks:
            chunks.append(json.dumps(res, ensure_ascii=False))
        return is_error, "\n".join(chunks)
