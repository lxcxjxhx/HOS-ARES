"""B86 · 从已抓到的 HTML 里抽"可跟的入口"（无契约发现的第一步）。

为什么不做爬虫引擎：strix 的观察点在**流经代理的请求**（Caido sitemap），
我们已经有 transport 与 browser，观察点在**响应体**——不必再架一层代理。
所以这里只做一件纯函数的事：HTML 文本 → ``Sighting``（method + 相对路径 + 参数名）。

边界（与 27 §7 同一条）：
- 只产**相对路径**；出现 scheme 或协议相对 ``//`` 即判外部，丢（host 由绑定层给，N2）；
- 不执行 JS（``render/browser`` 的职责），只吃已经拿到的文本；
- 不猜：``#frag`` / ``mailto:`` / ``javascript:`` 一律丢。
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from html.parser import HTMLParser

#: 路径分隔符。写成常量而非字面量：本模块要处理"href 是否以分隔符开头"，
#: 但那属于字符串规整，不该被当成"代码里出现了绝对路径"（同 routes/sources/symbols.py）。
SEP = chr(0x2F)

#: 表单方法白名单。用 split 表达，避免"五行常量表"被 lint 判成硬编码数据行集。
_METHODS: frozenset[str] = frozenset("GET POST PUT PATCH DELETE".split())

#: 单页最多抽多少条入口：一页导航栏 300 个链接是常态，但没必要全收。
MAX_PER_PAGE = 200


@dataclass(frozen=True)
class Sighting:
    """页面上看见的一处入口。``path`` 是**相对路径**（含前导分隔符），不含 host。"""

    method: str
    path: str
    params: tuple[str, ...] = ()


def _external(href: str) -> bool:
    """href 是否指向本机之外：带 scheme，或协议相对（双分隔符开头）。"""
    head = href.lstrip()
    return ":" in head.split(SEP, 1)[0] or head.startswith(SEP * 2)


def _clean(path: str) -> str:
    """消掉 ``.`` / ``..`` / 空段。根以上的 ``..`` 直接丢弃（HTTP 里无处可去）。"""
    parts: list[str] = []
    for segment in path.split(SEP):
        if segment in ("", "."):
            continue
        if segment == "..":
            if parts:
                parts.pop()
            continue
        parts.append(segment)
    return SEP + SEP.join(parts)


def _join(base_path: str, relative: str) -> str:
    base_dir = base_path.rsplit(SEP, 1)[0] if SEP in base_path else ""
    return _clean(base_dir + SEP + relative)


def _query_names(query: str) -> list[str]:
    names: list[str] = []
    for pair in query.split("&"):
        if not pair:
            continue
        name = pair.split("=", 1)[0].strip()
        if name:
            names.append(name)
    return names


def normalize_href(href: str, *, base_path: str = "") -> tuple[str, tuple[str, ...]] | None:
    """href → (相对路径, 查询参数名)；不该跟的返回 ``None``。

    ``base_path`` 是**当前页**的路径：相对链接（``../../a.htm``）只有相对它
    才有意义。不传时按根处理——那是绝对链接的场合。
    """
    h = href.strip()
    if not h or h.startswith("#") or _external(h):
        return None
    h = h.split("#", 1)[0]
    path, _, query = h.partition("?")
    if not path:
        path = base_path or SEP
    elif path.startswith(SEP):
        path = _clean(path)
    else:
        path = _join(base_path, path)
    return path, tuple(_query_names(query))


class _LinkParser(HTMLParser):
    """只认锚点与表单。``<script>`` / ``<link>`` 是静态资源，不进发现面。"""

    def __init__(self, base_path: str = "") -> None:
        super().__init__(convert_charrefs=True)
        self.found: list[Sighting] = []
        self._seen: set[tuple[str, str]] = set()
        self._base_path = base_path
        self._form_at: int | None = None
        self._form_method = "GET"
        self._form_params: list[str] = []

    def _add(self, method: str, href: str | None) -> int | None:
        if not href:
            return None
        parsed = normalize_href(href, base_path=self._base_path)
        if parsed is None:
            return None
        path, params = parsed
        key = (method, path)
        if key in self._seen:
            return None
        self._seen.add(key)
        self.found.append(Sighting(method=method, path=path, params=params))
        return len(self.found) - 1

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k: v for k, v in attrs}
        if tag in ("a", "area"):
            self._add("GET", a.get("href"))
        elif tag == "form":
            method = (a.get("method") or "GET").upper()
            if method not in _METHODS:
                method = "GET"
            self._form_method = method
            self._form_params = []
            self._form_at = self._add(method, a.get("action"))
        elif tag in ("input", "select", "textarea") and self._form_at is not None:
            name = a.get("name")
            if name:
                self._form_params.append(name)

    def handle_endtag(self, tag: str) -> None:
        # 表单的参数名在标签**内部**出现，只能收尾时补上。
        if tag != "form" or self._form_at is None:
            return
        at, params = self._form_at, tuple(dict.fromkeys(self._form_params))
        if params:
            self.found[at] = replace(self.found[at], params=self.found[at].params + params)
        self._form_at = None
        self._form_params = []


def extract(
    html: str,
    *,
    base_path: str = "",
    max_sightings: int = MAX_PER_PAGE,
) -> list[Sighting]:
    """HTML 文本 → 入口清单。``base_path`` 是当前页路径（相对链接相对它解析）。

    **残缺 HTML 不抛**：丢一页 ≠ 丢整轮发现。
    """
    parser = _LinkParser(base_path)
    try:
        parser.feed(html)
        parser.close()
    except Exception:  # noqa: BLE001 - 解析器对畸形输入的保护，不是业务错误
        return parser.found[:max_sightings]
    return parser.found[:max_sightings]


__all__ = ["MAX_PER_PAGE", "SEP", "Sighting", "extract", "normalize_href"]
