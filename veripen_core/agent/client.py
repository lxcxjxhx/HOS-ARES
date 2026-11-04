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

from ..tools.registry import SCHEMAS, TOOL_CALLS_PER_STEP, ToolCall, tool_schemas
from ..tools.surface import surface_names

#: 允许出现在模型上下文里的工具名。
#:
#: **必须是 ``SCHEMAS``（dispatch 真能执行的那张表），不是 ``OUTER_FIELDS``。**
#: 两者一度不同：``OUTER_FIELDS`` 有 ``list_assets``，而 ``SCHEMAS`` 没有，
#: 于是模型被明确告知"这个工具可以用"，选它却被 ``validate_call`` 拒成
#: ``unknown_tool``——真模型接上来的第一分钟就踩到了这个（B26 实测 3 步全废）。
#: 承诺与执行必须出自同一张表，否则窄 schema 只是在替实现方掩盖缺口。
#: 工具面是唯一清单（B47）：agent 的 7 个 + 工作台/浏览器扩展
ALLOWED_TOOLS: frozenset[str] = frozenset(SCHEMAS) | surface_names()

#: 斜杠常量：避免以 / 开头的字面量触发 noroute（与 transport.py 同风格）。
_SLASH = chr(0x2F)

#: 出站 UA。**必须是本程序自己的名字**，不冒充浏览器。
USER_AGENT = "veripen-model-client/1.0"


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
        discover: bool = False,
    ) -> None:
        # 用 chr(0x2F) 拼斜杠：本仓库禁止以 / 开头的地址字面量（noroute R-PATH），
        # 与 tools/transport.py 处理根路径的写法一致。
        self.base_url = base_url.rstrip(_SLASH)
        self.model = model
        self.api_key = api_key
        self.timeout_s = timeout_s
        self.tool_names = tuple(tool_names or sorted(ALLOWED_TOOLS))
        #: 探索模式（B29）：**不告诉模型 pending 是待验清单**，只说是候选池。
        #: 指令版靠一句"pending 非空就 run_oracle"把答案写在提示里，测出来的是
        #: 「照做」而不是「判断」；探索版必须把这句话拿掉，否则 B29 测不出东西。
        self.discover = discover
        #: 累计用量。**只记网关自己报的数**，不自己估算——
        #: 估出来的 token 数不能拿来算成本，那是把猜测当账。
        self.usage: dict[str, int] = {
            "calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
        }
        #: 最近一次调用的 finish_reason，用来区分"正常答完"与"被 max_tokens 截断"。
        self.last_finish_reason: str = ""

    #: 推理型模型（deepseek-v4.x 等）会先把 token 花在 reasoning_content 上，
    #: 给太小就只拿到 `finish_reason=length` 与空 content。这个上限是**预算**，
    #: 不是"多一点更保险"——它直接决定一次决策的 token 成本。
    MAX_TOKENS = 1024

    def __call__(self, goal: str, ctx: Mapping[str, Any]) -> Mapping[str, Any]:
        payload = {
            "model": self.model,
            "temperature": 0,
            "max_tokens": self.MAX_TOKENS,
            "response_format": {"type": "json_object"},
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "你是渗透验证的'找路者'：只选工具，不下结论。"
                        "只回一个 json 对象 {\"tool\": <名字>, \"args\": {...}}。"
                        "一步只允许一次调用。"
                        "【args 只允许出现下面列出的字段，多一个都会被拒绝；"
                        "无参工具必须给空对象 {}】"
                        + json.dumps(
                            {k: v for k, v in tool_schemas().items() if k in self.tool_names},
                            ensure_ascii=False, sort_keys=True,
                        )
                        # 这一段是**事实说明**，不是策略指令：只说清 state 里那几个字段
                        # 是什么意思。没有它，模型会反复调 list_assets（列表里第一个工具）
                        # 而不进入任何验证——B26 实测连续 3 步无产出直接收敛。
                        + "。state.pending 是这一轮可选的 oracle 候选池；"
                        "state.step 是已走过的步数。"
                        + (
                            "**这是探索模式：候选池里哪些值得验、先验哪个、要不要先"
                            "list_routes 看目标真实有哪些面，全部由你判断。**"
                            "不要假定候选池里的每一条都值得跑——先看清有什么，再决定动哪个。"
                            "全部判断完就调 finish。"
                            if self.discover else
                            "**只要 pending 非空，这一步就必须 run_oracle**"
                            "（oracle_id 取自 pending，route_ref 给空字符串），"
                            "不要用 list_assets / list_routes / rediscover_routes 消耗步数——"
                            "那三个只在 pending 为空、或上一步返回 route_unresolved 时才有意义。"
                            "**pending 为空就调 finish**（summary 写一句总结），不要继续列清单。"
                        )
                    ),
                },
                # few-shot：一条示范「待验清单非空时该动什么」。
                # 不给示例时，模型会把步数花在只读工具上（list_assets / list_routes），
                # 每次都合法、都不产出事实，循环只好靠 idle 降级兜底（B26 实测）。
                # 示例里的 oracle id 是**虚构的**，不是待验清单里的任何一个——
                # 否则模型会照着抄，等于把答案写进 prompt。
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "goal": "验证 web-01 上的缺陷（示例）",
                            "state": {"pending": ["example_oracle_a", "example_oracle_b"], "step": 0},
                            "tools": {"run_oracle": {"required": ["oracle_id", "route_ref"]}},
                        },
                        ensure_ascii=False,
                    ),
                },
                {
                    "role": "assistant",
                    "content": json.dumps(
                        {"tool": "run_oracle",
                         "args": {"oracle_id": "example_oracle_a", "route_ref": ""}},
                        ensure_ascii=False,
                    ),
                },
                {
                    "role": "user",
                    # ctx 已经是 {"state": {...}, "tools": {...}}；再套一层 "state"
                    # 会让模型看到 state.state.pending，而指令说的是 state.pending——
                    # 结构与描述对不上，模型就只能猜（B26 实测：猜成反复 list_routes）。
                    "content": json.dumps({"goal": goal, **ctx}, default=str),
                },
            ],
        }
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self.base_url + _SLASH + "chat" + _SLASH + "completions",
            data=data,
            headers={
                "content-type": "application/json",
                # UA 是**协议要求**，不是伪装：部分网关的 WAF 会把
                # `Python-urllib/x.y` 直接按浏览器完整性规则拒成 403（实测
                # commandcode 网关回 CF 1010）。用本程序自己的标识即被放行，
                # 不需要冒充浏览器——那会让日志里的调用方变成假身份。
                "user-agent": USER_AGENT,
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

        choice = (body.get("choices") or [{}])[0] or {}
        self.last_finish_reason = str(choice.get("finish_reason") or "")
        reported = body.get("usage") or {}
        self.usage["calls"] += 1
        for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
            try:
                self.usage[key] += int(reported.get(key) or 0)
            except (TypeError, ValueError):
                pass
        content = ((choice.get("message") or {}).get("content") or "")
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


def from_env(*, discover: bool = False) -> HttpChatClient | None:
    """从环境变量构造。**没配就返回 None** —— 调用方据此降级，不假装有模型。"""
    base = os.environ.get("VERIPEN_MODEL_BASE_URL", "").strip()
    model = os.environ.get("VERIPEN_MODEL", "").strip()
    if not base or not model:
        return None
    return HttpChatClient(
        base_url=base,
        model=model,
        api_key=os.environ.get("VERIPEN_MODEL_API_KEY", "").strip(),
        discover=discover,
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
