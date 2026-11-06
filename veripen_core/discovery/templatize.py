"""B86 · 具体路径 → 归一模板（观察面必须归一，否则 1000 个 id 就是 1000 条"面"）。

归一规则只有三条，**宁可少归一**：把 ``users`` 也归成 ``{p}``，
会让两条本来不同的面撞成同一个 ref，而 ref 是胶囊与报告的锚点。

1. 全数字段（``123``、``2024``）；
2. UUID / 长十六进制段（≥8 位且只含 ``0-9a-f-``）；
3. 含 ``@`` 的邮箱形段。

参数名按出现顺序给 ``p1..pn``——与 ``veripen_contract.parser.normalize_template``
的 ``{p}`` 归一配套：模板里保留真名，结构身份仍由归一模板算。

ponytail: 三条启发式的天花板是"语义 id"（``user-alice``、``order_2024_x``）不归一，
它们会各占一条面。真实目标上出现面数爆炸时，再按"同前缀段数 ≥N 且值各不相同"补规则。
"""

from __future__ import annotations

SEP = chr(0x2F)

#: 段被视为十六进制 id 的最短长度。短于它的（``v1``、``api``）一律当字面量。
_HEX_MIN = 8

_HEX_CHARS = frozenset("0123456789abcdef-")


def _split_ext(segment: str) -> tuple[str, str]:
    """``9825.htm`` → ``("9825", ".htm")``；没有短后缀就整段返回。

    为什么要拆：CMS 的文章页形如 ``/info/12/9825.htm``，不拆的话每个文章号
    都是"另一条面"——一次首页抓取就能攒出几十条同模板实例，
    覆盖率分母当场失真。
    """
    head, dot, tail = segment.rpartition(".")
    if dot and 1 <= len(tail) <= 5 and tail.isalnum() and head:
        return head, dot + tail
    return segment, ""


def _is_dynamic(segment: str) -> bool:
    if not segment:
        return False
    if segment.isdigit():
        return True
    if "@" in segment:
        return True
    low = segment.lower()
    return len(low) >= _HEX_MIN and all(c in _HEX_CHARS for c in low)


def templatize(path: str) -> tuple[str, tuple[str, ...]]:
    """``/api/users/123`` → ``(/api/users/{p1}, ("p1",))``。

    参数名按**出现顺序**编号，不带原始段（原始 id 是数据，不是结构）。
    结构身份随后由 ``normalize_template`` 统一成 ``{p}``，编号不外泄。
    """
    out: list[str] = []
    names: list[str] = []
    for segment in path.split(SEP):
        stem, ext = _split_ext(segment)
        if _is_dynamic(stem):
            name = f"p{len(names) + 1}"
            names.append(name)
            out.append("{" + name + "}" + ext)
        else:
            out.append(segment)
    template = SEP.join(out)
    if len(template) > 1 and template.endswith(SEP):
        template = template.rstrip(SEP) or SEP
    return template, tuple(names)


__all__ = ["templatize"]
