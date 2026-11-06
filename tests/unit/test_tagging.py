"""B94 · 观测面打标签：没有契约时标签从哪来（`H27`）。

只推能从观测事实得到的那几条。路径字符串**不参与**语义推断——
`/admin` 不代表 admin_function，猜错会让报告把"没测"写成"测过"。

| 事实 | 标签 |
|---|---|
| GET + 1 个路径参数 + JSON 字段 | `object_read` |
| GET + 分页参数 | `object_list` |
| 任意方法 + 查询参数 | `param_injectable` |
| GET + text/* + 查询参数 | `file_read` |
| 写方法 + 有 body | `content_write` |
"""

from __future__ import annotations

import pytest

from veripen_core.routes.sources.base import ObservedRecord, candidate_from_record
from veripen_core.routes.tagging import infer_tags
from veripen_core.routes.types import RouteProvenance

S = chr(0x2F)


def p(*segments: str) -> str:
    return S + S.join(segments)


def test_single_object_read_needs_json_fields_and_one_path_param() -> None:
    assert infer_tags(
        method="GET",
        template=p("api", "users", "{p1}"),
        content_type="application/json",
        fields=("id", "name"),
    ) == ("object_read",)


def test_object_read_absent_without_fields() -> None:
    assert infer_tags(
        method="GET", template=p("api", "users", "{p1}"), content_type="application/json"
    ) == ()


def test_query_params_make_param_injectable() -> None:
    assert infer_tags(method="GET", template=p("search"), param_names=("q",)) == (
        "param_injectable",
    )


def test_text_endpoint_with_params_is_file_read_candidate() -> None:
    tags = infer_tags(
        method="GET",
        template=p("download"),
        param_names=("name",),
        content_type="text/plain",
    )
    assert tags == ("param_injectable", "file_read")


def test_write_with_body_is_content_write() -> None:
    assert infer_tags(
        method="POST", template=p("api", "notes"), content_type="application/json"
    ) == ("content_write",)


def test_path_words_are_not_semantics() -> None:
    """路径里带 admin 不等于 admin_function：那是猜。"""
    assert infer_tags(method="GET", template=p("admin", "users")) == ()


@pytest.mark.parametrize("method", ["GET", "HEAD"])
def test_reads_only_for_read_methods(method: str) -> None:
    assert "content_write" not in infer_tags(method=method, template=p("x"), content_type="application/json")


def test_candidate_from_record_carries_structural_tag_source() -> None:
    rec = ObservedRecord(
        method="GET",
        template=p("api", "users", "{p1}"),
        param_names=("p1",),
        content_type="application/json",
        fields=("id", "name"),
    )
    cand = candidate_from_record(rec, source=RouteProvenance.CRAWL, prefix="{base}")
    assert cand.template == "{base}" + p("api", "users", "{p1}")
    assert cand.response_shape["semantic_tags"] == ["object_read", "param_injectable"]
    # 来源必须标出来：契约给的 0.95 与观测推的 0.80 不能混
    assert set(cand.response_shape["tag_sources"].values()) == {"structural"}
