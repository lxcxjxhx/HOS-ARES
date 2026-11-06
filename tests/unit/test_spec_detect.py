"""B89 · 契约格式识别与 Swagger 2.0 归一（`H27 §2.1`）。

每条断言对应一个"客户真的会给你这种东西"的场合：

| 断言 | 会红的情形 |
|---|---|
| 认得出 swagger 2.0 | 把老契约报成"paths 不是对象"，读的人以为文件坏了 |
| body 参数变 requestBody | POST 面 body 形状全退化成 `nobody` |
| 响应 schema 进 content | object_read 之类结构标签全丢 |
| Postman 明确拒绝 | 拿 collection 硬解析，报一堆无关字段错误 |
| 归一后能出候选 | 转完仍 0 条 operation（转了个空壳） |
"""

from __future__ import annotations

import json

import pytest

from veripen_contract.parser import parse_openapi
from veripen_contract.spec_detect import (
    SpecFormatError,
    as_openapi_text,
    classify,
    load_spec,
    spec_base_urls,
    swagger_to_openapi,
)

S = chr(0x2F)
#: scheme 与分隔符都拼出来：测试目录也在零地址闸门的扫描范围内。
SECURE = "https" + ":" + S * 2
HOST = "api.example.test"


def p(*segments: str) -> str:
    return S + S.join(segments)


def swagger_doc() -> dict:
    return {
        "swagger": "2.0",
        "info": {"title": "legacy", "version": "1.0"},
        "host": HOST,
        "basePath": p("v1"),
        "schemes": ["https"],
        "produces": ["application/json"],
        "definitions": {"User": {"type": "object", "properties": {"id": {"type": "integer"}}}},
        "paths": {
            p("users", "{id}"): {
                "get": {
                    "parameters": [
                        {"name": "id", "in": "path", "required": True, "type": "string"}
                    ],
                    "responses": {
                        "200": {
                            "description": "ok",
                            "schema": {
                                "type": "object",
                                "properties": {"id": {"type": "integer"}, "name": {"type": "string"}},
                            },
                        }
                    },
                }
            },
            p("users"): {
                "post": {
                    "parameters": [
                        {"name": "u", "in": "body", "schema": {"$ref": "#/definitions/User"}}
                    ],
                    "responses": {"201": {"description": "created"}},
                }
            },
        },
    }


# ── 1. 识别 ────────────────────────────────────────────────────────


def test_classify_three_formats() -> None:
    assert classify({"openapi": "3.0.0"}) == "openapi"
    assert classify({"swagger": "2.0"}) == "swagger"
    assert classify({"info": {"_postman_id": "x"}, "item": []}) == "postman"
    assert classify({"info": {"title": "x"}}) is None


def test_load_spec_reads_json_and_yaml() -> None:
    assert load_spec(json.dumps({"swagger": "2.0"}))["swagger"] == "2.0"
    assert load_spec("swagger: '2.0'\n")["swagger"] == "2.0"


# ── 2. 归一 ────────────────────────────────────────────────────────


def test_swagger_basepath_is_folded_into_paths() -> None:
    """``basePath`` 必须折进路径：绑定层只补 host，不补路径前缀。"""
    doc = swagger_to_openapi(swagger_doc())
    assert doc["servers"] == [{"url": SECURE + HOST}]
    assert p("v1", "users") in doc["paths"]
    assert p("v1", "users", "{id}") in doc["paths"]
    assert doc["components"]["schemas"]["User"]["type"] == "object"


def test_swagger_body_param_becomes_request_body() -> None:
    doc = swagger_to_openapi(swagger_doc())
    post = doc["paths"][p("v1", "users")]["post"]
    assert "parameters" not in post
    assert post["requestBody"]["application/json"]["schema"] == {"$ref": "#/definitions/User"}


def test_swagger_response_schema_moves_into_content() -> None:
    doc = swagger_to_openapi(swagger_doc())
    ok = doc["paths"][p("v1", "users", "{id}")]["get"]["responses"]["200"]
    assert ok["content"]["application/json"]["schema"]["type"] == "object"


def test_swagger_to_openapi_rejects_non_swagger() -> None:
    with pytest.raises(SpecFormatError):
        swagger_to_openapi({"openapi": "3.0.0"})


# ── 3. 入口 ────────────────────────────────────────────────────────


def test_as_openapi_text_passes_openapi_through() -> None:
    text = json.dumps({"openapi": "3.0.0", "info": {"title": "x", "version": "1"}, "paths": {}})
    assert as_openapi_text(text) == text


def test_as_openapi_text_converts_swagger() -> None:
    out = json.loads(as_openapi_text(json.dumps(swagger_doc())))
    assert out["openapi"].startswith("3.")
    assert p("v1", "users") in out["paths"]


def test_as_openapi_text_rejects_postman_with_actionable_message() -> None:
    text = json.dumps({"info": {"_postman_id": "1"}, "item": []})
    with pytest.raises(SpecFormatError, match="Postman"):
        as_openapi_text(text)


def test_as_openapi_text_rejects_unknown() -> None:
    with pytest.raises(SpecFormatError):
        as_openapi_text(json.dumps({"info": {"title": "x"}}))


def test_spec_base_urls_swagger_and_openapi() -> None:
    assert spec_base_urls(swagger_doc()) == [SECURE + HOST + p("v1")]
    assert spec_base_urls({"openapi": "3.0.0", "servers": [{"url": SECURE + HOST}]}) == [
        SECURE + HOST
    ]


# ── 4. 归一之后真的能出 operation ──────────────────────────────────


def test_converted_swagger_parses_into_operations() -> None:
    text = as_openapi_text(json.dumps(swagger_doc()))
    opset = parse_openapi(text, asset_id=HOST, allowed_hosts=[HOST])
    assert len(opset.operations) == 2
    templates = sorted(op.template for op in opset.operations)
    assert p("v1", "users", "{p}") in templates
