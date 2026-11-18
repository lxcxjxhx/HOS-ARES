"""B12.6 · 自由动作 ``act.*``（35 §4/§5）：**能打，但打出来的只是线索**。

六个动作，三类沙箱要求：

| # | action | 网络 | 产物 |
|---|---|---|---|
| A1 | ``http_raw`` | 出网白名单 | Lead |
| A2 | ``http_fuzz`` | 同 A1 + 速率 | Lead |
| A3 | ``exec_tool`` | 容器 + 只读根 | Lead |
| A4 | ``decode`` | 无网络 | Lead |
| A5 | ``compare`` | 无网络 | Lead |
| A6 | ``compose`` | 无网络，**只产文本不发** | Lead |

三条铁律，每条都可执行检查：

1. **动作层不许碰判定**：本包内 ``import Verdict / Judgement`` 即不得存在
   （``test_actions_cannot_import_verdict`` 用 AST 扫）。
2. **地址不入库**：``http_raw``/``http_fuzz`` 只收 ``route_ref``；
   ``exec_tool`` 只收 ``target_ref``，argv 由**数组模板**展开，绝不字符串拼接。
3. **每个动作都要产 lead**：产不出线索的动作是白打，直接算失败。
"""

from __future__ import annotations

import base64
import binascii
import gzip
import hashlib
import json
import shlex
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping, Sequence

from ..routes.addresses import contains_address
from ..routes.types import RouteBinding

#: A3 白名单。**不含** msfconsole / sliver / sqlmap / 任意脚本解释器（35 §4）。
BINARY_WHITELIST: frozenset[str] = frozenset({"curl", "jq", "nmap", "nuclei", "ffuf"})

#: 每个二进制的 argv 模板。**先写成串、导入期切成 tuple**：
#: 切出来的每个元素就是一个 argv 参数，展开时槽位值里有空格也仍然算**一个**参数
#: （这正是数组展开相对字符串拼接的全部意义）。
_ARGV_TEXT: Mapping[str, str] = {
    "nmap": "{binary} -sV --top-ports {ports} {host}",
    "nuclei": "{binary} -u {url} -silent -severity critical,high",
    "ffuf": "{binary} -u {url} -w {wordlist} -mc 200,403",
    "curl": "{binary} -sS -o - {url}",
    "jq": "{binary} . {file}",
}
ARGV_TEMPLATES: Mapping[str, tuple[str, ...]] = {
    k: tuple(v.split()) for k, v in _ARGV_TEXT.items()
}

#: 模板里允许被替换的占位符。出现别的就是模板写错。
TEMPLATE_SLOTS: frozenset[str] = frozenset({"binary", "host", "url", "ports", "wordlist", "file"})

#: 沙箱要求（05 §1 边界表）。只收**需要隔离**的那几个：
#: 没列出的动作即"零网络纯计算"，不需要容器，也不需要出网白名单。
SANDBOX_REQUIREMENTS: Mapping[str, str] = {
    "http_raw": "egress_allowlist",
    "http_fuzz": "egress_allowlist",
    "exec_tool": "container_ro_root",
}

#: 零网络动作：剩下的三个。两者并集必须覆盖全部六个动作。
OFFLINE_ACTIONS: frozenset[str] = frozenset({"decode", "compare", "compose"})


class ActionError(RuntimeError):
    """动作越界：地址字面量、非白名单二进制、或试图产出结论。**fail-closed**。"""

    code = "VP-8006"


# ══════════════════════════════════════════════════════════════════
# 线索：动作的唯一产物
# ══════════════════════════════════════════════════════════════════
@dataclass(frozen=True)
class LeadDraft:
    """一条线索草案。**没有 verdict 字段**——动作层不产出结论。"""

    source_action: str
    raw_ref: str
    summary: str
    verifiable_by: tuple[str, ...]
    selector_hint: str | None = None
    confidence_hint: float = 0.0

    @property
    def is_dead(self) -> bool:
        """``verifiable_by`` 为空 = 垃圾线索，产生即 expired，不占预算（35 §2 硬约束 1）。"""
        return not self.verifiable_by

    def assert_no_verdict(self) -> None:
        banned = {"verdict", "severity", "state", "cwe", "confidence"}
        leaked = banned & set(self.__dict__)
        if leaked:
            raise ActionError(f"LeadDraft 出现结论字段 {sorted(leaked)}（动作层不判定）")


@dataclass(frozen=True)
class ActionResult:
    action: str
    lead: LeadDraft
    output: Mapping[str, Any] = field(default_factory=dict)

    @property
    def expired(self) -> bool:
        """线索不可闭合 → 直接作废，**不占预算**（35 §2 硬约束 1）。

        注意这不是"动作失败"：动作跑成了，只是它产出的东西验不了。
        把两者混为一谈，报告里就会多出一批假的 error。
        """
        return self.lead.is_dead


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _raw_ref(action: str, payload: str) -> str:
    """完整输出落盘引用。**只存哈希，不存地址**。"""
    return f"out/act/{action}-{_sha(payload)[:16]}.json"


# ══════════════════════════════════════════════════════════════════
# A1 http_raw / A2 http_fuzz —— 只收 route_ref
# ══════════════════════════════════════════════════════════════════
def _assert_not_address(value: str, *, field_name: str) -> None:
    """任何看起来像地址的入参直接拒。**模型不许提供地址，只能提供 ref。**

    判定交给路由层 ``routes.addresses``：不透明 ref 自身也带 scheme 分隔符，
    自己在这里写「含分隔符就拒」会连合法 ref 一起拒掉——地址语法只能有一个来源。
    """
    if contains_address(value):
        raise ActionError(f"{field_name} 出现地址字面量：{value[:40]!r}（只能用 route_ref）")


def http_raw(
    *,
    route_ref: str,
    method: str,
    headers: Mapping[str, str] | None = None,
    body: str = "",
    send: Callable[[RouteBinding, str, Mapping[str, str], str], Mapping[str, Any]],
    binding: RouteBinding,
) -> ActionResult:
    """用**已解析绑定**发任意方法/头/体。地址在这里只以 ``RouteBinding`` 形式存在。"""
    _assert_not_address(route_ref, field_name="route_ref")
    if binding.ref != route_ref:
        raise ActionError(f"route_ref 与绑定不符：{route_ref[:24]!r} != {binding.ref[:24]!r}")
    resp = send(binding, method, headers or {}, body)
    status = int(resp.get("status") or 0)
    interesting = bool(resp.get("interesting"))
    lead = LeadDraft(
        source_action="http_raw",
        raw_ref=_raw_ref("http_raw", json.dumps(resp, sort_keys=True, default=str)),
        summary=f"{method} {route_ref} → {status}" + ("（异常）" if interesting else ""),
        verifiable_by=tuple(resp.get("verifiable_by") or ()),
        selector_hint=binding.semantic_tags[0] if binding.semantic_tags else None,
        confidence_hint=0.6 if interesting else 0.2,
    )
    lead.assert_no_verdict()
    return ActionResult("http_raw", lead, {"status": status})


def http_fuzz(
    *,
    route_ref: str,
    param: str,
    variants: Sequence[str],
    rate_per_s: int,
    send: Callable[[RouteBinding, str, str], Mapping[str, Any]],
    binding: RouteBinding,
) -> ActionResult:
    """参数变形喷射。**速率是入参也是硬约束**：并发不等于压测（04 §7）。"""
    _assert_not_address(route_ref, field_name="route_ref")
    if param in ("host", "url", "path"):
        raise ActionError(f"不许直接喷地址字段 {param!r}")
    if rate_per_s <= 0:
        raise ActionError("rate_per_s 必须为正：不给速率的喷射就是压测")
    if len(variants) > rate_per_s * 60:
        raise ActionError(f"变体数 {len(variants)} 超过一分钟速率上限 {rate_per_s * 60}")

    diffs: list[str] = []
    for i, v in enumerate(variants):
        _assert_not_address(v, field_name=f"variants[{i}]")
        r = send(binding, param, v)
        if r.get("differential"):
            diffs.append(f"{param}={v[:16]}→{r.get('status')}")
    lead = LeadDraft(
        source_action="http_fuzz",
        raw_ref=_raw_ref("http_fuzz", "|".join(diffs) or "no-diff"),
        summary=f"喷射 {len(variants)} 个变体，差分 {len(diffs)} 处",
        verifiable_by=("sqli_bool_diff", "sqli_error_based", "xss_reflected_ctx") if diffs else (),
        selector_hint=binding.semantic_tags[0] if binding.semantic_tags else None,
        confidence_hint=min(1.0, 0.3 + 0.1 * len(diffs)),
    )
    lead.assert_no_verdict()
    return ActionResult("http_fuzz", lead, {"diffs": len(diffs)})


# ══════════════════════════════════════════════════════════════════
# A3 exec_tool —— 白名单 + 数组模板 + 零地址
# ══════════════════════════════════════════════════════════════════
def build_argv(binary: str, *, slots: Mapping[str, str], target_ref: str) -> list[str]:
    """把模板展开成 argv**数组**。出现任何字面地址即拒（35 §5）。

    ``target_ref`` 只用于记录来源，**不参与 argv**——真实 host 由框架在
    ``resolve_target`` 里注入（resolver 内，模型看不见）。
    """
    if binary not in BINARY_WHITELIST:
        raise ActionError(
            f"{binary!r} 不在白名单 {sorted(BINARY_WHITELIST)}（35 §4：二次编程载体一律不放）"
        )
    template = ARGV_TEMPLATES.get(binary)
    if template is None:
        raise ActionError(f"{binary!r} 没有 argv 模板——没有模板就没有可审计的命令行")
    for key, val in slots.items():
        if key not in TEMPLATE_SLOTS:
            raise ActionError(f"未知模板占位符 {{{key}}}")
        _assert_not_address(str(val), field_name=f"slots.{key}")
    out: list[str] = []
    for part in template:
        name = part[1:-1] if part.startswith("{") and part.endswith("}") else None
        if name is None:
            out.append(part)
            continue
        if name == "binary":
            out.append(binary)
        elif name in slots:
            out.append(str(slots[name]))
        else:
            raise ActionError(f"模板 {binary} 需要 {{{name}}} 但未提供")
    _assert_not_address(target_ref, field_name="target_ref")
    return out


def exec_tool(
    *,
    binary: str,
    preset: str,
    target_ref: str,
    slots: Mapping[str, str],
    run: Callable[[Sequence[str]], Mapping[str, Any]],
) -> ActionResult:
    """跑白名单二进制。``slots`` 里的真实地址由框架填，模型只给 ref 与 preset。"""
    argv = build_argv(binary, slots=slots, target_ref=target_ref)
    result = run(argv)
    text = str(result.get("stdout") or "")
    hits = list(result.get("hits") or ())
    lead = LeadDraft(
        source_action="exec_tool",
        raw_ref=_raw_ref("exec_tool", text),
        summary=f"{binary} {preset} @ {target_ref}：{len(hits)} 个命中",
        verifiable_by=tuple(result.get("verifiable_by") or ()),
        selector_hint=str(result.get("selector_hint") or "") or None,
        confidence_hint=0.5 if hits else 0.1,
    )
    lead.assert_no_verdict()
    return ActionResult("exec_tool", lead, {"argv_len": len(argv), "hits": len(hits)})


# ══════════════════════════════════════════════════════════════════
# A4 decode / A5 compare / A6 compose —— 零网络
# ══════════════════════════════════════════════════════════════════
def decode(*, blob: str, how: str) -> ActionResult:
    """解码/解包。结构线索（例如 JWT 的三段、protobuf 的字段号）。"""
    handlers = {"base64", "jwt", "gzip", "json"}
    if how not in handlers:
        raise ActionError(f"不支持的解码方式 {how!r}（可选 {sorted(handlers)}）")
    try:
        if how == "base64":
            text = base64.b64decode(blob + "=" * (-len(blob) % 4)).decode("utf-8", "replace")
        elif how == "gzip":
            text = gzip.decompress(base64.b64decode(blob + "=" * (-len(blob) % 4))).decode(
                "utf-8", "replace")
        elif how == "jwt":
            parts = blob.split(".")
            if len(parts) < 2:
                raise ActionError("JWT 至少要有两段")
            head = base64.urlsafe_b64decode(parts[0] + "=" * (-len(parts[0]) % 4))
            text = head.decode("utf-8", "replace")
        else:
            text = json.dumps(json.loads(blob), ensure_ascii=False, sort_keys=True)
    except (binascii.Error, ValueError, OSError) as exc:
        raise ActionError(f"解码失败（{how}）：{exc}") from exc

    structural = _structural_hint(text)
    lead = LeadDraft(
        source_action="decode",
        raw_ref=_raw_ref("decode", text),
        summary=f"{how} 解出 {len(text)} 字符" + (f"，结构线索：{structural}" if structural else ""),
        verifiable_by=("jwt_replay",) if how == "jwt" else ("graphql_introspection",),
        selector_hint="token_issue" if how == "jwt" else None,
        confidence_hint=0.4 if structural else 0.15,
    )
    lead.assert_no_verdict()
    return ActionResult("decode", lead, {"chars": len(text)})


def _structural_hint(text: str) -> str:
    for key in ('"errors"', '"data"', '"alg"', '"kid"'):
        if key in text:
            return key.strip('"')
    return ""


def compare(*, left: Mapping[str, Any], right: Mapping[str, Any]) -> ActionResult:
    """两份响应结构化差分。疑似 IDOR / 信息泄露的线索来源。"""
    lk = set(left)
    rk = set(right)
    only_left = sorted(lk - rk)
    only_right = sorted(rk - lk)
    changed = sorted(k for k in lk & rk if left[k] != right[k])
    signal = bool(only_left or only_right or changed)
    lead = LeadDraft(
        source_action="compare",
        raw_ref=_raw_ref("compare", json.dumps([left, right], sort_keys=True, default=str)),
        summary=f"差 {len(only_left)}/{len(only_right)}/{len(changed)}（左独/右独/键值异）",
        verifiable_by=("idor_cross_account", "bola_functional") if signal else (),
        selector_hint="object_read" if signal else None,
        confidence_hint=0.55 if signal else 0.1,
    )
    lead.assert_no_verdict()
    return ActionResult("compare", lead, {"signal": signal})


def compose(*, template: str, slots: Mapping[str, str]) -> ActionResult:
    """**只生成文本，不发**（35 §4 A6）。发与拼是两步，中间必须过门控。"""
    if contains_address(template):
        raise ActionError("compose 只能在**绑定内**拼载荷，不许出现地址")
    body = template
    for key, val in slots.items():
        body = body.replace("{" + key + "}", str(val))
    lead = LeadDraft(
        source_action="compose",
        raw_ref=_raw_ref("compose", body),
        summary=f"生成候选载荷 {len(body)} 字符（未发送）",
        verifiable_by=("xss_reflected_ctx", "sqli_bool_diff", "open_redirect"),
        confidence_hint=0.2,
    )
    lead.assert_no_verdict()
    return ActionResult("compose", lead, {"chars": len(body), "sent": False})


# ══════════════════════════════════════════════════════════════════
# 统一入口
# ══════════════════════════════════════════════════════════════════
ACTIONS: Mapping[str, Callable[..., ActionResult]] = {
    "http_raw": http_raw,
    "http_fuzz": http_fuzz,
    "exec_tool": exec_tool,
    "decode": decode,
    "compare": compare,
    "compose": compose,
}


def sandbox_mode(action: str) -> str:
    """沙箱要求（05 §1 边界表）：没有对应容器就不许跑。

    零网络动作返回 ``none``——它们真的不需要隔离能力，但**仍然是动作**，
    仍然要产 lead，仍然不许判定。
    """
    if action in SANDBOX_REQUIREMENTS:
        return SANDBOX_REQUIREMENTS[action]
    if action in OFFLINE_ACTIONS:
        return "none"
    raise ActionError(f"未知动作 {action!r}")


def run_action(action: str, **kwargs: Any) -> ActionResult:
    """跑一个动作。返回结果里带 ``expired`` 标记 —— 死线索要**记下来**，不是扔掉。"""
    fn = ACTIONS.get(action)
    if fn is None:
        raise ActionError(f"未知动作 {action!r}")
    return fn(**kwargs)


__all__ = [
    "ACTIONS",
    "ARGV_TEMPLATES",
    "BINARY_WHITELIST",
    "OFFLINE_ACTIONS",
    "SANDBOX_REQUIREMENTS",
    "TEMPLATE_SLOTS",
    "ActionError",
    "ActionResult",
    "LeadDraft",
    "build_argv",
    "compare",
    "compose",
    "decode",
    "exec_tool",
    "http_fuzz",
    "http_raw",
    "run_action",
    "sandbox_mode",
]
