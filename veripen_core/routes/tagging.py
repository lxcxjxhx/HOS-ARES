"""B94 · 观测面打标签：没有契约时，标签从"看见的形状"推。

为什么需要这一层：`plan()` 只给**带标签**的 operation 出候选，而标签原本只来自
契约（显式 `x-veripen-tags` 或 `_structural_tags` 读 operation 形状）。
无契约路径上这两样都没有，于是 discover 攒到的面进不了 oracle 选择——
`veripen discover` 与 `veripen run` 之间断在"标签"这一步。

这里只做**能从观测事实推出来的**那几条，宁少不多：

| 事实 | 标签 | 依据 |
|---|---|---|
| GET + 1 个路径参数 + JSON 字段 | `object_read` | 单对象读 |
| GET + 分页参数 | `object_list` | 列表读 |
| 任意方法 + 查询参数 | `param_injectable` | 参数可注入候选 |
| GET + text/* + 查询参数 | `file_read` | 读文件型端点 |
| 写方法 + 有 body | `content_write` | 写面（默认被排除，但标签要真） |

**不从路径名字符串推**（`/admin` 不代表 admin_function）：那是猜，而且猜错会让
报告把"没测"写成"测过"。路径只用于 `templatize` 的结构归一，不用于语义。
"""

from __future__ import annotations

from typing import Sequence

SEP = chr(0x2F)

#: 分页参数名（与 contract parser 的 PAGING_PARAM_NAMES 同口径）
PAGING_NAMES: frozenset[str] = frozenset("limit offset page cursor per_page page_size".split())

#: 写方法：有 body 就打 content_write
WRITE_METHODS: frozenset[str] = frozenset("POST PUT PATCH".split())

#: 文本型 content-type 前缀
TEXT_PREFIX = "text/"


def _path_param_count(template: str) -> int:
    return sum(1 for seg in template.split(SEP) if seg.startswith("{") and seg.endswith("}"))


def infer_tags(
    *,
    method: str,
    template: str,
    param_names: Sequence[str] = (),
    content_type: str = "",
    fields: Sequence[str] = (),
) -> tuple[str, ...]:
    """从观测事实推标签。**顺序稳定**（便于比较与快照）。"""
    verb = method.upper()
    ctype = content_type.lower()
    tags: list[str] = []
    # HTML 文档本身也是一种"面"：响应头类判定（CSP/HSTS…）只需要它。
    # 以前这类页面没有任何标签，于是永远记 face_without_oracle。
    if "html" in (content_type or "").lower():
        tags.append("html_document")

    if verb in ("GET", "HEAD"):
        if _path_param_count(template) == 1 and fields:
            tags.append("object_read")
        if any(name.lower() in PAGING_NAMES for name in param_names):
            tags.append("object_list")
        if param_names:
            tags.append("param_injectable")
            if ctype.startswith(TEXT_PREFIX):
                tags.append("file_read")
    elif verb in WRITE_METHODS and content_type:
        tags.append("content_write")

    return tuple(dict.fromkeys(tags))


__all__ = ["PAGING_NAMES", "WRITE_METHODS", "infer_tags"]
