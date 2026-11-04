"""DOM 大纲归一化（B46）：把"渲染结果"变成可比较的指纹。

为什么不能直接比 HTML：属性顺序、空白、随机 id、时间戳每页都抖，直接比会永远不等。
归一只做**能解释的**几件事：丢易变属性、排属性序、折叠空白、去注释。
不动语义结构（标签、层级、可见文本），这样"页面变了"仍然能被抓到。
"""

from __future__ import annotations

import hashlib
import re
from html.parser import HTMLParser

#: 每次渲染都可能不同、但**不承载语义**的属性
VOLATILE_ATTRS = frozenset({
    "nonce", "style", "data-reactroot", "data-reactid", "data-v-inspector",
    "integrity", "crossorigin", "fetchpriority",
})

#: 只保留这些"有语义"的属性（其余一律丢，避免把构建噪声写进指纹）
KEEP_ATTRS = frozenset({"id", "class", "role", "href", "name", "type", "aria-label", "for"})

#: 巨大的内联脚本/样式不进入大纲（它们的哈希另算）
SKIP_TAGS = frozenset({"script", "style", "noscript", "link", "meta"})

_WS = re.compile(r"\s+")


class _Outline(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.depth = 0
        self.lines: list[str] = []
        self._skip = 0            # 处在跳过标签内时，连正文也不收

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in SKIP_TAGS:
            self._skip += 1
            return
        if self._skip:
            return
        kept = []
        for name, value in sorted(attrs):
            if name in VOLATILE_ATTRS or name not in KEEP_ATTRS:
                continue
            text = _WS.sub(" ", (value or "")).strip()
            kept.append(f"{name}={text}")
        suffix = (" " + " ".join(kept)) if kept else ""
        self.lines.append("  " * self.depth + f"<{tag}{suffix}>")
        self.depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in SKIP_TAGS:
            self._skip = max(0, self._skip - 1)
            return
        if self._skip:
            return
        self.depth = max(0, self.depth - 1)
        self.lines.append("  " * self.depth + f"</{tag}>")

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        text = _WS.sub(" ", data).strip()
        if text:
            self.lines.append("  " * self.depth + text)


def outline(html: str) -> str:
    """HTML → 归一化大纲（行式、可比对、可审阅）。"""
    parser = _Outline()
    parser.feed(html)
    parser.close()
    return "\n".join(parser.lines)


def dom_sha256(html_or_outline: str) -> str:
    """大纲的指纹。调用方传 HTML 或已归一化大纲都可，结果一致。"""
    text = html_or_outline if "\n<" in html_or_outline else outline(html_or_outline)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def redact_url(url: str) -> str:
    """**地址不出控制台/证据**：只留"这是一个 ref 的前 8 位"与查询参数名。"""
    from urllib.parse import urlsplit

    parts = urlsplit(url)
    names = sorted({kv.split("=", 1)[0] for kv in parts.query.split("&") if kv})
    digest = hashlib.sha256(f"{parts.scheme}:{parts.netloc}:{parts.path}".encode()).hexdigest()[:8]
    return f"ref:{digest}" + (f"?{'&'.join(names)}" if names else "")


__all__ = ["KEEP_ATTRS", "VOLATILE_ATTRS", "SKIP_TAGS", "dom_sha256", "outline", "redact_url"]
