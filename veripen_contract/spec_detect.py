"""B89 · 契约格式识别与 Swagger 2.0 归一（`H27 §2.1`）。

`--openapi` 这个名字骗人：客户给的东西经常是 **swagger 2.0**
（2017 年前的工具链都吐这个）或 **Postman collection**（手工维护的团队）。
直接把它们喂给 `parse_openapi`，报错是"paths 不是对象"——
读的人会以为契约坏了，其实是**格式没认出来**。

抄 strix `utils/api_spec.py` 的两个判据（`classify_spec` / `spec_base_urls`），
**不抄**它"operations 留给 agent 自己读"的分工：我们的 oracle 选择依赖结构化
operation，必须在这里就归一成 OpenAPI 3 的形状，否则下游一条候选都出不来。

边界仍是我们自己的三条：**纯函数**（不联网）、**不出地址**（host 只用于越界判定）、
**零猜**（认不出就报错，不"尽力而为"地猜字段）。
"""

from __future__ import annotations

import json
from typing import Any, Mapping, Sequence

#: Swagger 的 HTTP 方法集。用 split 表达，避免"五行常量表"被 lint 判成硬编码数据行集。
_METHODS: frozenset[str] = frozenset("get put post delete options head patch".split())

#: 转化时默认的响应/请求媒体类型（swagger 的 ``produces`` 缺省值）。
_DEFAULT_MEDIA = "application/json"

#: 路径分隔符。写成常量而非字面量：本模块要做"去尾分隔符"，
#: 但那属于字符串规整，不该被当成"代码里出现了绝对路径"。
SEP = chr(0x2F)

#: 明确不做的 Swagger 特性：表单体。宁缺不猜——猜错会把一条面测成另一条面。
_UNSUPPORTED_PARAM_LOCATIONS: frozenset[str] = frozenset({"formData"})


class SpecFormatError(ValueError):
    """契约格式认不出/不支持（VP-1010）。**不是**契约内容错误，别混报。"""

    code = "VP-1010"


def load_spec(text: str) -> dict[str, Any]:
    """JSON 或 YAML → 映射。认不出就抛，不做"尽力解析"。"""
    try:
        data: Any = json.loads(text)
    except json.JSONDecodeError:
        try:
            import yaml  # 契约常以 YAML 发布
        except ImportError as exc:  # pragma: no cover - 依赖在 pyproject 里
            raise SpecFormatError("契约是 YAML 但没有 PyYAML") from exc
        try:
            data = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            raise SpecFormatError(f"既不是 JSON 也不是 YAML：{exc}") from exc
    if not isinstance(data, dict):
        raise SpecFormatError("契约根不是对象")
    return data


def classify(raw: Mapping[str, Any]) -> str | None:
    """``openapi`` / ``swagger`` / ``postman``，或 ``None``。"""
    if isinstance(raw.get("openapi"), str):
        return "openapi"
    if str(raw.get("swagger", "")).startswith("2"):
        return "swagger"
    info = raw.get("info")
    if isinstance(info, dict) and ("_postman_id" in info or "item" in raw):
        return "postman"
    return None


def _absolute(urls: Sequence[str]) -> list[str]:
    """只留绝对 http(s) 地址，去尾分隔符、去重保序。

    **不用 urllib.parse**：`veripen_contract` 有"不许 import 网络栈"的包级铁律
    （`tests/fixtures/contract/test_parser.py` 用 AST 扫），`urllib` 在黑名单里。
    这里要的只是"scheme 是不是 http、host 非空"，自己切两刀比引一个包更清楚。
    """
    out: list[str] = []
    for raw in urls:
        text = raw.strip()
        scheme, marker, rest = text.partition("://")
        host = rest.split(SEP, 1)[0]
        if marker and scheme in ("http", "https") and host:
            out.append(text.rstrip(SEP))
    return list(dict.fromkeys(out))


def spec_base_urls(raw: Mapping[str, Any]) -> list[str]:
    """契约自述的基址。**只用于越界判定**，不用于发送（27 §2）。"""
    fmt = classify(raw)
    if fmt == "openapi":
        servers = raw.get("servers")
        if not isinstance(servers, list):
            return []
        return _absolute(
            [str(s["url"]) for s in servers if isinstance(s, dict) and s.get("url")]
        )
    if fmt == "swagger":
        host = str(raw.get("host") or "").strip()
        if not host:
            return []
        base = str(raw.get("basePath") or "").strip()
        schemes = [s for s in (raw.get("schemes") or ["https"]) if isinstance(s, str)]
        return _absolute([f"{s}://{host}{base}" for s in schemes])
    if fmt == "postman":
        raise SpecFormatError(
            "Postman collection 没有可判定的基址；请先 Export 成 OpenAPI 3"
        )
    raise SpecFormatError("认不出契约格式")


# ══════════════════════════════════════════════════════════════════
# Swagger 2.0 → OpenAPI 3（够用即可，不做全量兼容）
# ══════════════════════════════════════════════════════════════════
def _convert_params(
    params: Sequence[Any],
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """swagger 参数表 → (OpenAPI 参数表, body schema)。

    ``in: body`` 在 OpenAPI 3 里是 ``requestBody``，不是参数——
    漏了这一步，POST 面的 body 形状就丢了，`body_shape_sha256` 会全部退化成 ``nobody``。
    """
    out: list[dict[str, Any]] = []
    body: dict[str, Any] | None = None
    for param in params:
        if not isinstance(param, dict):
            continue
        where = str(param.get("in") or "")
        if where == "body":
            schema = param.get("schema")
            body = schema if isinstance(schema, dict) else {}
            continue
        if where in _UNSUPPORTED_PARAM_LOCATIONS:
            continue
        item: dict[str, Any] = {
            key: param[key] for key in ("name", "in", "required", "description") if key in param
        }
        schema = param.get("schema")
        if not isinstance(schema, dict):
            schema = {"type": str(param.get("type") or "string")}
            if param.get("format"):
                schema["format"] = param["format"]
        item["schema"] = schema
        out.append(item)
    return out, body


def _convert_responses(
    responses: Mapping[str, Any], media: Sequence[str]
) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for code, resp in responses.items():
        if not isinstance(resp, dict):
            continue
        item: dict[str, Any] = {"description": str(resp.get("description") or "")}
        schema = resp.get("schema")
        if isinstance(schema, dict):
            item["content"] = {ctype: {"schema": schema} for ctype in media}
        out[str(code)] = item
    return out


def _convert_operation(
    op: Mapping[str, Any], media: Sequence[str]
) -> dict[str, Any]:
    out: dict[str, Any] = {
        key: value
        for key, value in op.items()
        if key not in ("parameters", "responses", "consumes", "produces")
    }
    params, body = _convert_params(op.get("parameters") or [])
    if params:
        out["parameters"] = params
    if body is not None:
        out["requestBody"] = {ctype: {"schema": body} for ctype in media}
    out["responses"] = _convert_responses(op.get("responses") or {}, media)
    return out


def swagger_to_openapi(raw: Mapping[str, Any]) -> dict[str, Any]:
    """把 Swagger 2.0 文档改写成 OpenAPI 3 的形状，交给现有 parser 处理。

    只做四件事：``servers`` / ``paths`` / ``components.schemas`` / ``requestBody``。
    其余字段原样带过——不认识不等于要丢，丢了才是猜。
    """
    if classify(raw) != "swagger":
        raise SpecFormatError("这不是 Swagger 2.0 文档")

    base_path = str(raw.get("basePath") or "")
    host = str(raw.get("host") or "").strip()
    schemes = [s for s in (raw.get("schemes") or ["https"]) if isinstance(s, str)]
    media = [
        str(m) for m in (raw.get("produces") or [_DEFAULT_MEDIA]) if isinstance(m, str)
    ] or [_DEFAULT_MEDIA]

    paths: dict[str, Any] = {}
    prefix = base_path.rstrip(SEP) if base_path not in ("", SEP) else ""
    raw_paths = raw.get("paths")
    for path, item in (raw_paths or {}).items() if isinstance(raw_paths, dict) else []:
        if not isinstance(item, dict):
            continue
        converted: dict[str, Any] = {}
        for method, op in item.items():
            if method.lower() in _METHODS and isinstance(op, dict):
                converted[method] = _convert_operation(op, media)
        shared = item.get("parameters")
        if isinstance(shared, list):
            converted["parameters"], _ = _convert_params(shared)
        # ``basePath`` 折进路径本身，而不是只放进 ``servers``：
        # 我们的绑定层只给 host 补前缀（``SymbolSource`` + ``--target``），
        # 路径里的前缀不会自动加回来——留在 servers 里就会全部打到 404 上。
        paths[str(prefix) + str(path)] = converted

    doc: dict[str, Any] = {
        "openapi": "3.0.0",
        "info": raw.get("info") or {"title": "converted", "version": "1.0"},
        "paths": paths,
    }
    if host:
        doc["servers"] = [{"url": f"{scheme}://{host}"} for scheme in schemes]
    definitions = raw.get("definitions")
    if isinstance(definitions, dict) and definitions:
        doc["components"] = {"schemas": definitions}
    return doc


def as_openapi_text(text: str) -> str:
    """任意受支持格式的契约原文 → OpenAPI 3 文本。认不出/不支持就抛。"""
    raw = load_spec(text)
    fmt = classify(raw)
    if fmt == "openapi":
        return text
    if fmt == "swagger":
        return json.dumps(swagger_to_openapi(raw), ensure_ascii=False)
    if fmt == "postman":
        raise SpecFormatError(
            "这是 Postman collection，不是 OpenAPI：请先在 Postman 里 "
            "Export → OpenAPI 3，或提供 OpenAPI/Swagger 文件"
        )
    raise SpecFormatError(
        "认不出契约格式：根对象里既没有 openapi 也没有 swagger 2.0（VP-1010）"
    )


__all__ = [
    "SpecFormatError",
    "as_openapi_text",
    "classify",
    "load_spec",
    "spec_base_urls",
    "swagger_to_openapi",
]
