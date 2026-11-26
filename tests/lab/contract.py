"""把靶场自己的面清单导成一份**配套的 OpenAPI 契约**（B544）。

为什么需要：`tests/fixtures/contract/sample.json` 的路径与靶场响应形状对不上，
于是「契约 → oracle → capsule → 报告」这条链路**从来没有在真 socket 上跑通过**——
B540 只钉住了「不匹配时体面收场」。

靶场本来就有一份机器可读的面清单（`app.assets_json()`），`discovery` 用它；
这里把同一份清单转成 OpenAPI，让契约路径也能验。**不另写一份端点表**：
两份迟早对不上，而 `assets_json()` 是被 `H05 §3` 认作"靶场对外声明"的那一份。

转译规则（只做"声明 → 契约"的机械映射，不推断任何语义）：

| `assets_json()` 字段 | OpenAPI 落点 |
|---|---|
| `template` 里的 `{x}` | `path` 参数或 `query` 参数（按 `?` 前/后判定） |
| `params[].kind` | `schema.type`（int→integer，url→string+format=uri，其余 string） |
| `tags` | `x-hos-ares-tags`（契约显式标签，置信 0.95） |
| 无 params 的 GET | 无参数 operation（`protected_read`/`html_document` 这类） |
"""

from __future__ import annotations

import json
import re
from typing import Any, Mapping

_OPENAPI_HEADER: dict[str, Any] = {"openapi": "3.0.0"}

#: `{name}` 占位符
_SLOT = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")

#: `assets_json()['params'][].kind` → OpenAPI schema 片段
_SCHEMA_BY_KIND: dict[str, dict[str, Any]] = {
    "int": {"type": "integer"},
    "url": {"type": "string", "format": "uri"},
    "str": {"type": "string"},
}


def _split_template(template: str) -> tuple[str, list[tuple[str, str]]]:
    """`/api/search?q={q}` → (`/api/search`, [(q, query)])；路径参数记 `path`。"""
    path, _, query = template.partition("?")
    params: list[tuple[str, str]] = []
    for name in _SLOT.findall(path):
        params.append((name, "path"))
    for name in _SLOT.findall(query):
        params.append((name, "query"))
    return path, params


def _param_spec(name: str, where: str, kind: str) -> dict[str, Any]:
    spec: dict[str, Any] = {
        "name": name,
        "in": where,
        "required": where == "path",     # OpenAPI 硬规矩：path 参数必须 required
        "schema": _SCHEMA_BY_KIND.get(kind, {"type": "string"}),
    }
    return spec


def to_openapi(assets: Mapping[str, Any], *, base_url: str) -> dict[str, Any]:
    """把一份 `assets_json()` 转成 OpenAPI 3 文档对象。

    ``base_url`` 写进 `servers`：契约校验要求 `servers[].host` 属于本次 assets，
    所以调真 socket 时必须传靶场实际地址。
    """
    kinds: dict[tuple[str, str, str], str] = {}
    for op in assets.get("operations", []):
        method = str(op["method"]).lower()
        path, _ = _split_template(str(op["template"]))
        for p in op.get("params") or []:
            kinds[(method, path, str(p["name"]))] = str(p.get("kind", "str"))

    paths: dict[str, dict[str, Any]] = {}
    for op in assets.get("operations", []):
        method = str(op["method"]).lower()
        template = str(op["template"])
        path, slots = _split_template(template)
        params = [
            _param_spec(name, where, kinds.get((method, path, name), "str"))
            for name, where in slots
        ]
        operation: dict[str, Any] = {
            "operationId": f"{method}_{path.strip('/').replace('/', '_') or 'root'}",
            "responses": {"200": {"description": "ok"}},
            "x-hos-ares-tags": list(op.get("tags") or []),
        }
        if params:
            operation["parameters"] = params
        if method in ("post", "put", "patch"):
            # 写类：body 用 query 参数拼出来的那个字段名，够契约解析认出形状
            operation["requestBody"] = {
                "content": {"application/json": {"schema": {
                    "type": "object",
                    "properties": {p["name"]: {"type": "string"} for p in params},
                }}}
            }
        paths.setdefault(path, {})[method] = operation

    return {
        **_OPENAPI_HEADER,
        "info": {"title": f"lab-contract-{assets.get('asset_id', 'asset')}",
                 "version": "1.0.0"},
        "servers": [{"url": base_url}],
        "paths": paths,
    }


def dump(assets: Mapping[str, Any], *, base_url: str) -> str:
    return json.dumps(to_openapi(assets, base_url=base_url), ensure_ascii=False, indent=2)


__all__ = ["to_openapi", "dump"]
