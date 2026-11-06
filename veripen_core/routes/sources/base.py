"""来源适配器基类与数据形状（27 §3.2）。

地址常量不在这里：适配器只产出 ``RouteCandidate``（结构描述），
真正可执行的 ``RouteBinding`` 由 resolver 构造（N2）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from ..tagging import infer_tags
from ..types import RouteCandidate, RouteProvenance, RouteQuery


class SourceError(RuntimeError):
    """来源适配器内部异常：**只跳过本来源**，其余照常合并（27 §3.9）。"""

    def __init__(self, source: str, reason: str) -> None:
        super().__init__(f"{source}: {reason}")
        self.source = source
        self.reason = reason


@dataclass(frozen=True)
class ContractOp:
    """契约里的一个 operation（已完成结构归一，不含可定位常量）。"""

    method: str
    template: str
    param_names: tuple[str, ...] = ()
    auth_requirement: str | None = None
    body_schema: dict[str, Any] | None = None
    semantic_tags: tuple[str, ...] = ()


@dataclass(frozen=True)
class ObservedRecord:
    """既有流量证据里真实发生过的一次请求（模板已归一）。

    ``content_type`` / ``fields`` 是**看见的形状**：没有契约时，标签只能从这两样
    加上方法与参数推出来（见 ``routes/tagging.py``）。留空表示没观察到，不是"没有"。
    """

    method: str
    template: str
    param_names: tuple[str, ...] = ()
    auth_requirement: str | None = None
    body_schema: dict[str, Any] | None = None
    verifications: int = 1
    content_type: str = ""
    fields: tuple[str, ...] = ()


def candidate_from_record(
    rec: "ObservedRecord",
    *,
    source: RouteProvenance,
    prefix: str = "",
) -> RouteCandidate:
    """观测记录 → 候选。**标签只从看见的形状推**（见 ``routes/tagging.py``）。

    ``prefix`` 用来把相对模板挂到基址符号上（``{base}``）——符号替换留给
    ``SymbolSource`` 做，这里只拼字符串，不解析地址。
    """
    # 前缀里的符号（``{base}``）不是参数：把它算进 param_names 会让每条面
    # 都凭空多出 param_injectable，覆盖率与 oracle 选择一起失真。
    symbol_names = {
        name
        for name in (prefix.strip("{}") if prefix else "",)
        if name
    }
    names = tuple(n for n in rec.param_names if n not in symbol_names)
    tags = infer_tags(
        method=rec.method,
        template=rec.template,
        param_names=names,
        content_type=rec.content_type,
        fields=rec.fields,
    )
    shape: dict[str, Any] = {}
    if tags:
        # tag_sources 让报告区分"契约给的"与"观测推的"：后者是猜，要标出来。
        shape = {
            "semantic_tags": list(tags),
            "tag_sources": {tag: "structural" for tag in tags},
        }
    return RouteCandidate(
        method=rec.method,
        template=prefix + rec.template,
        param_names=names,
        auth_requirement=rec.auth_requirement,
        body_shape_sha256=body_shape_sha256(rec.body_schema),
        source=source,
        verifications=rec.verifications,
        response_shape=shape,
    )


class Source(Protocol):
    name: str
    provenance: RouteProvenance

    def collect(self, q: RouteQuery) -> list[RouteCandidate]: ...


def body_shape_sha256(body_schema: dict[str, Any] | None) -> str:
    """body 形状哈希；无 body 用常量 ``nobody``（27 §3.4）。"""
    import hashlib
    import json

    if not body_schema:
        return "nobody"
    payload = json.dumps(body_schema, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def matches_hints(c: RouteCandidate, q: RouteQuery) -> bool:
    """只做结构过滤：标签/参数形状/认证要求，绝不按“像不像”挑地址。"""
    h = q.hints
    if h.auth_requirement and c.auth_requirement != h.auth_requirement:
        return False
    tags = c.response_shape.get("semantic_tags") or ()
    if h.semantic_tag and tags and h.semantic_tag not in tags:
        return False
    return True


@dataclass
class StaticSource:
    """测试与 operator 用的静态来源：候选在构造时给定，collect 不联网。"""

    name: str
    provenance: RouteProvenance
    candidates: list[RouteCandidate] = field(default_factory=list)
    raise_error: str | None = None

    def collect(self, q: RouteQuery) -> list[RouteCandidate]:
        if self.raise_error:
            raise SourceError(self.name, self.raise_error)
        return [RouteCandidate(**{**vars(c)}) for c in self.candidates if matches_hints(c, q)]
