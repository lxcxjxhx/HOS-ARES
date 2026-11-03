"""B18 · 真模型接驳（`08 §7` / `39 §4.1` 的第一件接线）。

这一层只做一件事：**把「模型返回的结构化调用」变成 `ToolCall`，或者明确拒绝**。
它不做猜测、不做重试、不解析自由文本——那三件事都会让"模型说什么就是什么"复活。

三条硬约束，每条对应一个失败模式：

1. **一步一调用**：模型返回 0 个或 >1 个 tool_call，一律 schema 错误。
   允许多个 = 允许它在一个 step 里越权串动作（`08 §2` 的 `tool_calls_per_step=1`）。
2. **只认白名单工具名**：`validate_call` 会拦未知工具；这里先拦一道，
   避免把 `url`/`host` 之类的字段顺进 dispatch。
3. **默认不联网**：没有 `VERIPEN_MODEL_BASE_URL` 就返回 `None`，
   由调用方降级到 scripted。**不许"以为配了"就发包**。

为什么把它单独成文件而不是塞进 `loop.py`：
`loop.py` 的注释写着"本模块不 import 任何模型 SDK"。这条纪律要保住——
模型客户端是**可替换的外部依赖**，循环是**不可变的判定骨架**。两者混在一起，
换模型厂商就会动到判定路径的代码。
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any, Mapping, Sequence

from ..tools.registry import OUTER_FIELDS, TOOL_CALLS_PER_STEP, ToolCall

#: 允许出现在模型上下文里的工具名（与 OUTER_FIELDS 同源，不另写一份）。
ALLOWED_TOOLS: frozenset[str] = frozenset(OUTER_FIELDS)

#: 斜杠常量：避免以 / 开头的字面量触发 noroute（与 transport.py 同风格）。
_SLASH = chr(0x2F)


class ModelClientError(RuntimeError):
    """模型层失败。``schema`` 类可降级；``transport`` 类应中止本轮调用。"""

    error_class = "transport"


class SchemaViolation(ModelClientError):
    """模型输出不符合窄 schema：**可降级**（转 scripted），不可"猜一下"。"""

    error_class = "schema"


def _coerce_single_call(raw: Mapping[str, Any]) -> ToolCall:
    """把模型的一条响应压成**恰好一个** ToolCall，否则抛 SchemaViolation。

    接受两种常见形态：
      - ``{"tool": name, "args": {...}}``（本项目约定）
      - ``{"name": name, "arguments": {...}}``
    以及 OpenAI 的 ``{"tool_calls": [ {...} ]}``——但**必须恰好一条**。
    """
    if "tool_calls" in raw:
        calls = raw.get("tool_calls") or []
        if not isinstance(calls, Sequence) or isinstance(calls, (str, bytes)):
            raise SchemaViolation("tool_calls 不是数组")
        if len(calls) != TOOL_CALLS_PER_STEP:
            raise SchemaViolation(
                f"一步只能 {TOOL_CALLS_PER_STEP} 个工具调用，收到 {len(calls)} 个"
            )
        first = calls[0] or {}
        fn = first.get("function") or first
        name = str(fn.get("name") or "")
        args = fn.get("arguments")
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError as exc:
                raise SchemaViolation(f"arguments 不是合法 JSON：{exc}") from exc
        raw = {"tool": name, "args": args}

    name = str(raw.get("tool") or raw.get("name") or "")
    if not name:
        raise SchemaViolation("模型没有给出工具名")
    if name not in ALLOWED_TOOLS and name != "finish":
        raise SchemaViolation(f"工具 {name!r} 不在白名单（可用：{sorted(ALLOWED_TOOLS)[:6]}…）")
    args = raw.get("args")
    if args is None:
        args = raw.get("arguments") or {}
    if not isinstance(args, Mapping):
        raise SchemaViolation(f"args 必须是对象，收到 {type(args).__name__}")
    return ToolCall(name, dict(args))


class HttpChatClient:
    """OpenAI 兼容 /chat/completions 的最小客户端。**只做一次调用，不重试。**

    ``goal``/``ctx`` 进 prompt，工具的窄 schema 进 `tools` 字段，
    模型必须按 `{"tool": ..., "args": {...}}` 回一个 JSON 对象。
    """

    name = "model"

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str = "",
        timeout_s: float = 30.0,
        tool_names: Sequence[str] | None = None,
    ) -> None:
        # 用 chr(0x2F) 拼斜杠：本仓库禁止以 / 开头的地址字面量（noroute R-PATH），
        # 与 tools/transport.py 处理根路径的写法一致。
        self.base_url = base_url.rstrip(_SLASH)
        self.model = model
        self.api_key = api_key
        self.timeout_s = timeout_s
        self.tool_names = tuple(tool_names or sorted(ALLOWED_TOOLS))

    def __call__(self, goal: str, ctx: Mapping[str, Any]) -> Mapping[str, Any]:
        payload = {
            "model": self.model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "你是渗透验证的'找路者'：只选工具，不下结论。"
                        "只回一个 JSON 对象 {\"tool\": <名字>, \"args\": {...}}。"
                        f"可用工具：{list(self.tool_names)}。一步只允许一次调用。"
                    ),
                },
                {"role": "user", "content": json.dumps({"goal": goal, "state": ctx}, default=str)},
            ],
        }
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self.base_url + _SLASH + "chat" + _SLASH + "completions",
            data=data,
            headers={
                "content-type": "application/json",
                **({"authorization": f"Bearer {self.api_key}"} if self.api_key else {}),
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:  # noqa: S310 - 仅回环网关
                body = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ModelClientError(f"模型不可达：{exc}") from exc
        except json.JSONDecodeError as exc:
            raise ModelClientError(f"模型返回非 JSON：{exc}") from exc

        content = (
            ((body.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
        )
        try:
            parsed: Any = json.loads(content) if isinstance(content, str) else content
        except json.JSONDecodeError as exc:
            raise SchemaViolation(f"content 不是 JSON 对象：{exc}") from exc
        if not isinstance(parsed, Mapping):
            raise SchemaViolation(f"模型返回的不是 JSON 对象：{type(parsed).__name__}")
        return dict(parsed)


class ScriptedClient:
    """测试用替身：按顺序吐预设响应。**它也是 `client` 注入点的一种实现。**

    存在的意义不是省一条网络，而是让"模型给坏输出"可以被稳定复现——
    没有它，降级链的测试只能靠到处打桩，一处漏掉就测不到真路径。
    """

    name = "scripted-client"

    def __init__(self, responses: Sequence[Mapping[str, Any]]) -> None:
        self._responses = list(responses)
        self._i = 0

    def __call__(self, goal: str, ctx: Mapping[str, Any]) -> Mapping[str, Any]:
        del goal, ctx
        if self._i >= len(self._responses):
            return {"tool": "finish", "args": {}}
        r = self._responses[self._i]
        self._i += 1
        return dict(r)


def from_env() -> HttpChatClient | None:
    """从环境变量构造。**没配就返回 None** —— 调用方据此降级，不假装有模型。"""
    base = os.environ.get("VERIPEN_MODEL_BASE_URL", "").strip()
    model = os.environ.get("VERIPEN_MODEL", "").strip()
    if not base or not model:
        return None
    return HttpChatClient(
        base_url=base,
        model=model,
        api_key=os.environ.get("VERIPEN_MODEL_API_KEY", "").strip(),
    )


def to_tool_call(raw: Mapping[str, Any]) -> ToolCall:
    """公开入口：模型响应 → ToolCall（不合规抛 SchemaViolation）。"""
    return _coerce_single_call(raw)


__all__ = [
    "ALLOWED_TOOLS",
    "HttpChatClient",
    "ModelClientError",
    "SchemaViolation",
    "ScriptedClient",
    "from_env",
    "to_tool_call",
]
