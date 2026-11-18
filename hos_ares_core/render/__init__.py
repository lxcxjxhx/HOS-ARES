"""B46 渲染执行体：DOM 大纲 + 脱敏网络日志 + 截图摘要，**不产 verdict**。"""

from .browser import BrowserRenderer, NET_LOG_LIMIT, RenderResult
from .dom import KEEP_ATTRS, SKIP_TAGS, VOLATILE_ATTRS, dom_sha256, outline, redact_url

__all__ = [
    "BrowserRenderer",
    "KEEP_ATTRS",
    "NET_LOG_LIMIT",
    "RenderResult",
    "SKIP_TAGS",
    "VOLATILE_ATTRS",
    "dom_sha256",
    "outline",
    "redact_url",
]
