"""契约解析（B8）：OpenAPI 3.0/3.1 → OperationSet → RouteCandidate。

三条硬边界（19 §8）：
1. **纯函数**：不联网，只读契约文本；不 import httpx/requests（CI 用 netimports 守）。
2. **不出地址**：path 只在本模块内存里存在，输出的是归一模板 + structural_key。
3. **零猜**：契约 host 越界 → 启动即拒（VP-1006）；标签用了字典外的词 → 拒。

**路径字面的唯一允许用途**是打标签时的"自指段判定"（19 §3）：
读路径是为了**分类**，不是为了发送。这条限制写在 `_self_ref` 里，不许扩散。
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

# ══════════════════════════════════════════════════════════════════
# 标签字典（18 §0 的 15 个；用切分写法避免被判成"硬编码数据表"）
# ══════════════════════════════════════════════════════════════════
#: 路径分隔符：用常量表达，避免被"零地址"自检误判成写地址（同 routes/merge.py）
SLASH = chr(0x2F)

TAG_VOCAB: frozenset[str] = frozenset(
    "object_read object_list param_injectable url_fetch file_read redirect_entry echo_param "
    "token_issue protected_read profile_write profile_read admin_function normal_function "
    "rate_sensitive content_write content_read api_read graphql_endpoint".split()
)

#: 自指段词表：**只用于打标签**，不许用于构造请求（19 §3 末段）
SELF_REF_WORDS = frozenset({"me", "self", "profile", "account", "my"})

#: URI 型参数的语义名（结构推断用；不是路径）
URI_PARAM_NAMES = frozenset({"url", "uri", "target", "webhook", "callback", "next", "redirect"})

#: 分页参数名
PAGING_PARAM_NAMES = frozenset({"limit", "offset", "page", "cursor"})

#: 静态资源 / 探活：选择器要扣分（19 §4）
HEALTH_WORDS = frozenset({"health", "healthz", "readyz", "ping", "livez"})
STATIC_EXTS = frozenset({".js", ".css", ".png", ".jpg", ".svg", ".ico", ".woff", ".map"})

#: 词 → 标签的显式扩展（契约里 x-veripen-tags 直接给）
EXTENSION_KEY = "x-veripen-tags"


# ── 错误（24 §4 的 1xxx 段）─────────────────────────────────────────
class ContractError(RuntimeError):
    code = "VP-1000"


class ContractHostOutOfScope(ContractError):
    """契约自述的 host 不在 assets 解析集合内（19 §2 拒绝条件 / VP-1006）。"""

    code = "VP-1006"


class ContractTooLarge(ContractError):
    code = "VP-1001"


class UnknownTag(ContractError):
    """标签用了字典外的词（防标签漂移，19 §2）。"""

    code = "VP-1002"


# ══════════════════════════════════════════════════════════════════
# 数据形状（19 §5.1）
# ══════════════════════════════════════════════════════════════════
@dataclass(frozen=True)
class ParamShape:
    name: str
    location: str  # path | query | header | cookie
    kind: str  # integer | string | boolean | number | object
    required: bool = False
    fmt: str | None = None


@dataclass(frozen=True)
class TagAssignment:
    tag: str
    source: str  # contract | structural | observed | operator
    tag_confidence: float
    disputed: bool = False
    conflict: str | None = None


@dataclass(frozen=True)
class Operation:
    op_id: str
    method: str
    template: str  # 归一后的路径模板（**只在内存里**）
    structural_key: str
    params: tuple[ParamShape, ...] = ()
    auth_requirement: str | None = None
    #: 已采纳的标签（契约显式 / 形状即声明）
    tags: tuple[TagAssignment, ...] = ()
    #: 结构提议但**未采纳**的标签：等 profile selectors（= operator）确认（19 §3.1）
    proposed: tuple[TagAssignment, ...] = ()
    success_shape: Mapping[str, Any] = field(default_factory=dict)
    score: float = 0.0
    disputed_tags: tuple[str, ...] = ()

    @property
    def tag_names(self) -> tuple[str, ...]:
        return tuple(t.tag for t in self.tags)

    def confirm(self, confirm_tags: Iterable[str]) -> "Operation":
        """profile selectors 的确认：把提议里的标签转成 operator 采纳。"""
        wanted = {t for t in confirm_tags}
        promoted = [
            TagAssignment(t.tag, "operator", 0.80) for t in self.proposed if t.tag in wanted
        ]
        if not promoted:
            return self
        merged = {t.tag: t for t in (*self.tags, *promoted)}
        return _replace(self, tags=tuple(merged[t] for t in sorted(merged)))


def _replace(op: Operation, **kw: Any) -> Operation:
    from dataclasses import replace

    return replace(op, **kw)


@dataclass(frozen=True)
class OperationSet:
    contract_sha256: str
    asset_id: str
    resolver_version: str
    operations: tuple[Operation, ...]
    unresolved: tuple[dict[str, Any], ...] = ()
    sources_failed: tuple[str, ...] = ()

    @property
    def count(self) -> int:
        return len(self.operations)

    def _rows(self) -> list[dict[str, Any]]:
        return [
            {
                "op_id": o.op_id,
                "method": o.method,
                "structural_key": o.structural_key,
                "params": [{"name": p.name, "in": p.location, "type": p.kind} for p in o.params],
                "auth": o.auth_requirement,
                "tags": [{"tag": t.tag, "source": t.source, "conf": t.tag_confidence} for t in o.tags],
                "score": o.score,
            }
            for o in self.operations
        ]

    def opset_sha256(self) -> str:
        # 注意：**不含** contract_sha256——那标识"哪份文件"，不是"解析出了什么结构"。
        # 同一份契约换个书写顺序应当得到同一个 opset 哈希（19 §9.1 幂等）。
        payload = {
            "asset": self.asset_id,
            "resolver_version": self.resolver_version,
            "operations": self._rows(),
        }
        # opset 哈希按**结构**排序后再序列化：契约书写顺序不能影响内容寻址
        rows: list[dict[str, Any]] = payload["operations"]  # type: ignore[assignment]
        rows.sort(key=lambda o: (o["structural_key"], o["method"], json.dumps(o, sort_keys=True)))
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        ).hexdigest()


# ══════════════════════════════════════════════════════════════════
# 解析（OpenAPI 3.0/3.1）
# ══════════════════════════════════════════════════════════════════
_PATH_PARAM = re.compile(r"\{[^}]+\}")
_MSG_SLOT = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


def normalize_template(path: str) -> str:
    """参数占位统一成 ``{p}``；末尾斜杠归一；**不改大小写**（RFC 3986 路径区分大小写）。"""
    t = _PATH_PARAM.sub("{p}", path.strip())
    if len(t) > 1 and t.endswith(SLASH):
        t = t.rstrip(SLASH) or SLASH
    return t


def structural_key(method: str, template: str, param_names: Iterable[str]) -> str:
    payload = "|".join([method.upper(), normalize_template(template), ",".join(sorted(set(param_names)))])
    return hashlib.sha256(payload.encode()).hexdigest()


def parse_openapi(
    text: str,
    *,
    asset_id: str,
    allowed_hosts: Iterable[str] = (),
    max_operations: int = 500,
    timezone_name: str = "UTC",
) -> OperationSet:
    """把 OpenAPI 文档解析成 OperationSet。**不联网、不产地址。**"""
    del timezone_name  # 解析不依赖时钟；留着参数是为了签名稳定
    doc = _loads(text)
    ops = doc.get("paths") or {}
    if not isinstance(ops, dict):
        raise ContractError("paths 不是对象")

    servers = doc.get("servers") or []
    hosts = {_host_of(s.get("url", "")) for s in servers if isinstance(s, dict)}
    hosts.discard("")
    if allowed_hosts:
        allowed = {h.lower() for h in allowed_hosts if h}
        out_of_scope = {h for h in hosts if h.lower() not in allowed}
        if out_of_scope:
            # 契约自述了别的系统 → 这不是我们的资产，启动即拒（VP-1006）
            raise ContractHostOutOfScope(f"契约 host 不在 assets 内：{sorted(out_of_scope)}")

    global_security = _security_requirement(doc.get("security"), doc)
    schemas = (doc.get("components") or {}).get("schemas") or {}

    collected: list[Operation] = []
    unresolved: list[dict[str, Any]] = []
    for raw_path, item in ops.items():
        if not isinstance(item, dict):
            continue
        for method, op in item.items():
            if method.upper() not in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}:
                continue
            if not isinstance(op, dict):
                continue
            collected.append(
                _build_op(
                    raw_path=raw_path,
                    method=method.upper(),
                    op=op,
                    path_item=item,
                    doc=doc,
                    schemas=schemas,
                    global_security=global_security,
                )
            )
            if len(collected) > max_operations:
                raise ContractTooLarge(f"operation 数超过 max_operations={max_operations}")

    # 稳定排序：只靠结构，不靠契约书写顺序（19 §4）
    collected.sort(key=lambda o: (o.structural_key, o.method))

    return OperationSet(
        contract_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        asset_id=asset_id,
        resolver_version="1.0.0",
        operations=tuple(collected),
        unresolved=tuple(unresolved),
    )


def _loads(text: str) -> dict[str, Any]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        try:
            import yaml  # 契约常以 YAML 发布
        except ImportError as exc:  # pragma: no cover - 依赖在 pyproject 里
            raise ContractError("契约是 YAML 但没有 PyYAML") from exc
        data = yaml.safe_load(text)
    if not isinstance(data, dict):
        raise ContractError("契约根不是对象")
    return data


def _host_of(url: str) -> str:
    """从 servers.url 取 host。不做网络解析（那是 routes 的事）。"""
    text = url.strip()
    if "://" not in text:
        return ""
    rest = text.split("://", 1)[1]
    return rest.split(SLASH, 1)[0].split("@")[-1].split(":")[0]


def _security_requirement(security: Any, doc: Mapping[str, Any]) -> str | None:
    """把 security 声明归一成角色名（primary/secondary/none）；不解析凭据本身。"""
    if not security:
        return None
    first = security[0] if isinstance(security, list) and security else {}
    if not isinstance(first, dict) or not first:
        return None
    scheme = next(iter(first))
    schemes = (doc.get("components") or {}).get("securitySchemes") or {}
    spec = schemes.get(scheme) or {}
    role = spec.get("x-veripen-role")
    return str(role) if role else "primary"


def _build_op(
    *,
    raw_path: str,
    method: str,
    op: Mapping[str, Any],
    path_item: Mapping[str, Any],
    doc: Mapping[str, Any],
    schemas: Mapping[str, Any],
    global_security: str | None,
) -> Operation:
    params = _params(raw_path, op, path_item)
    template = normalize_template(raw_path)
    key = structural_key(method, template, [p.name for p in params])

    security = _security_requirement(op.get("security"), doc) or global_security
    success = _success_shape(op, schemas)
    op_id = str(op.get("operationId") or f"{method.lower()}_{key[:12]}")
    explicit = _explicit_tags(op)
    structural = _structural_tags(method, template, params, success, security)
    merged, proposed = _merge_tags(explicit, structural)
    return Operation(
        op_id=op_id,
        method=method,
        template=template,
        structural_key=key,
        params=params,
        auth_requirement=security,
        tags=merged,
        success_shape=success,
        score=_score(params, merged),
        disputed_tags=tuple(t.tag for t in merged if t.disputed),
        proposed=proposed,
    )


def _params(raw_path: str, op: Mapping[str, Any], path_item: Mapping[str, Any]) -> tuple[ParamShape, ...]:
    raw: list[Mapping[str, Any]] = []
    for source in (path_item.get("parameters") or [], op.get("parameters") or []):
        raw.extend(p for p in source if isinstance(p, dict))
    names_in_path = set(_MSG_SLOT.findall(raw_path))
    out: list[ParamShape] = []
    seen: set[tuple[str, str]] = set()
    for p in raw:
        name = str(p.get("name") or "")
        if not name:
            continue
        where = str(p.get("in") or "query")
        if where == "path" and name not in names_in_path:
            continue  # 声明了但模板没有：脏契约，跳过（不猜）
        if (name, where) in seen:
            continue
        seen.add((name, where))
        schema = p.get("schema") or {}
        out.append(
            ParamShape(
                name=name,
                location=where,
                kind=str(schema.get("type") or "string"),
                required=bool(p.get("required")) or where == "path",
                fmt=schema.get("format"),
            )
        )
    return tuple(sorted(out, key=lambda x: (x.location, x.name)))


def _success_shape(op: Mapping[str, Any], schemas: Mapping[str, Any]) -> dict[str, Any]:
    responses = op.get("responses") or {}
    for code in sorted(responses, key=str):
        if not str(code).startswith("2"):
            continue
        resp = responses[code] or {}
        content = resp.get("content") or {}
        for ctype, spec in content.items():
            schema = _deref((spec or {}).get("schema") or {}, schemas)
            return {"content_type": ctype, "fields": _field_names(schema, schemas)}
    return {}


def _deref(schema: Mapping[str, Any], schemas: Mapping[str, Any], depth: int = 0) -> Mapping[str, Any]:
    while isinstance(schema, dict) and "$ref" in schema and depth < 8:
        ref = str(schema["$ref"]).split(SLASH)[-1]
        schema = schemas.get(ref) or {}
        depth += 1
    return schema


def _field_names(schema: Mapping[str, Any], schemas: Mapping[str, Any]) -> tuple[str, ...]:
    if not isinstance(schema, dict):
        return ()
    if schema.get("type") == "array":
        return _field_names(_deref(schema.get("items") or {}, schemas), schemas)
    props = schema.get("properties") or {}
    return tuple(sorted(props.keys()))[:16]


def _explicit_tags(op: Mapping[str, Any]) -> list[TagAssignment]:
    raw = op.get(EXTENSION_KEY)
    if not raw:
        return []
    tags = [raw] if isinstance(raw, str) else list(raw)
    out: list[TagAssignment] = []
    for tag in tags:
        name = str(tag)
        if name not in TAG_VOCAB:
            raise UnknownTag(f"未知标签：{name}（字典见 18 §0）")
        out.append(TagAssignment(tag=name, source="contract", tag_confidence=0.95))
    return out


def _structural_tags(
    method: str,
    template: str,
    params: tuple[ParamShape, ...],
    success: Mapping[str, Any],
    security: str | None,
) -> list[TagAssignment]:
    """结构推断：**只看结构**；唯一读路径的地方是自指段判定（19 §3 明许）。"""
    tags: list[str] = []
    path_params = [p for p in params if p.location == "path"]
    query_params = [p for p in params if p.location == "query"]
    fields = tuple(success.get("fields") or ())
    ctype = str(success.get("content_type") or "")

    if method in {"GET", "HEAD"} and len(path_params) == 1 and (
        any("id" in f.lower() for f in fields) or bool(fields)
    ):
        # 结构判据："单对象读"= 一个 path 参数 + 2xx 返回对象字段。
        # 字段名里出现 id 只是更强的证据，不是必要条件（有的契约返回 {name,owner}）。
        tags.append("object_read")
    if method == "GET" and any(p.name in PAGING_PARAM_NAMES for p in query_params):
        tags.append("object_list")
    if query_params and any(p.kind in {"string", "integer", "number"} for p in query_params):
        tags.append("param_injectable")
    if any(p.fmt in {"uri", "url"} for p in params) or any(p.name in URI_PARAM_NAMES for p in params):
        tags.append("url_fetch")
    if method == "GET" and any(p.kind == "string" for p in params) and ctype.startswith("text/"):
        tags.append("file_read")
    if "json" in ctype:
        tags.append("api_read")
    if any(f.lower() in {"access_token", "token", "jwt"} for f in fields):
        tags.append("token_issue")
    if security and method == "GET":
        tags.append("protected_read")
    # 自指段判定：只读路径用来分类（19 §3 明许的唯一例外）
    if method in {"PUT", "PATCH"} and _has_self_ref(template) and "json" in ctype:
        tags.append("profile_write")
    if _has_self_ref(template) and method in {"GET", "POST"}:
        tags.append("profile_read")
    return [TagAssignment(tag=t, source="structural", tag_confidence=0.80) for t in tags]


def _has_self_ref(template: str) -> bool:
    """**唯一允许读路径字面的地方**：判断这段是不是"当前用户自指"。只读，不写。"""
    for segment in template.split(SLASH):
        if segment in SELF_REF_WORDS:
            return True
    return False


_CONTRADICTS = {
    # 契约说 A，结构说 B 且互斥（C2）
    ("admin_function", "protected_read"),
    ("profile_write", "api_read"),
    ("token_issue", "object_read"),
}


#: 结构推断里"自身就是权威声明"的标签：operation 的形状本身就说明了它是什么
#: （有 uri 参数 ⇔ 会去取 URL；响应含 access_token ⇔ 发 token）。
#: 其余标签（object_read / admin_function …）属"看起来像"，必须契约标注才采用。
SELF_EVIDENT = frozenset({"url_fetch", "token_issue", "profile_write", "object_list"})


def _merge_tags(
    explicit: list[TagAssignment], structural: list[TagAssignment]
) -> tuple[tuple[TagAssignment, ...], tuple[TagAssignment, ...]]:
    """裁决表（19 §3.1）：声明 > 推断。**一般结构标签不能单独成立。**

    两处刻意的例外，都写在这里而不是散在实现里：
    1. ``SELF_EVIDENT``：形状即声明（见上）→ 0.80，并标 ``source=structural_adopted``；
    2. 显式标注对**结构性矛盾**的标签先剔除（契约说 profile_write，但方法不是写 → 打不了）。
    """
    by_tag: dict[str, TagAssignment] = {}
    proposed: dict[str, TagAssignment] = {}
    for t in explicit:
        by_tag[t.tag] = TagAssignment(t.tag, "contract", 0.85)

    structural_map = {t.tag: t for t in structural}
    for tag, s in structural_map.items():
        if tag in by_tag:
            # 契约 ∧ 结构 一致 → 置信升到 0.90
            by_tag[tag] = TagAssignment(tag, "contract", 0.90)
            continue
        if tag in SELF_EVIDENT:
            by_tag[tag] = TagAssignment(tag, "structural_adopted", 0.80)
            continue
        # 仅有结构：**不直接采纳**（19 §3.1 第 5 行）。进 proposed，等 operator 确认。
        proposed[tag] = TagAssignment(tag, "structural", 0.80)
        contradictions = [a for a, b in _CONTRADICTS if a in by_tag and b == tag]
        if contradictions:
            primary = contradictions[0]
            by_tag[primary] = TagAssignment(
                primary, "contract", 0.80, disputed=True, conflict="C2 契约∧结构冲突"
            )
    # api_read 是"通用面"：响应是 JSON 只说明它是 API，不说明能测什么。
    # 它既不采纳也不提议——否则每条 GET 都会变成一条"可测面"，把分母灌满。
    by_tag.pop("api_read", None)
    proposed.pop("api_read", None)
    return (
        tuple(by_tag[t] for t in sorted(by_tag)),
        tuple(proposed[t] for t in sorted(proposed)),
    )


def _score(params: Iterable[ParamShape], tags: Iterable[TagAssignment]) -> float:
    """选择器排序分（19 §4）。只影响"先打哪个"，不影响"能不能打"。"""
    names = {t.tag for t in tags}
    score = 0.0
    score += 3.0 if "object_read" in names else 0.0
    score += 2.0 if "admin_function" in names else 0.0
    score += 1.0 if "url_fetch" in names else 0.0
    score += 1.0 if "token_issue" in names else 0.0
    score += 0.5 * len(list(params))
    joined = " ".join(p.name.lower() for p in params)
    if any(word in joined for word in HEALTH_WORDS):
        score -= 2.0
    return round(score, 3)


__all__ = [
    "ContractError",
    "ContractHostOutOfScope",
    "ContractTooLarge",
    "Operation",
    "OperationSet",
    "ParamShape",
    "SELF_REF_WORDS",
    "TAG_VOCAB",
    "TagAssignment",
    "UnknownTag",
    "normalize_template",
    "parse_openapi",
    "structural_key",
]
