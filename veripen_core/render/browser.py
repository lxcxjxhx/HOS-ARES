"""浏览器渲染执行体（B46）：把"目标页渲染成可比对的东西"。

边界（`H02 §3.1`）：

- **只渲染**：DOM 大纲 + 脱敏网络日志 + 截图摘要；**永不产 verdict**；
- **每次新 context**：不共享 cookie/localStorage，避免"上一个动作的登录态"偷偷影响这一次；
- **超时即 BLOCKED**：渲染失败不退化成"没发现"；
- **宿主降级是显式的**：本机没有容器，`isolation="host"` 必须被调用方与证据同时看见。

依赖：`playwright`（可选）。没装时 `available()` 为 False，调用返回 `blocked`。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Mapping

from .dom import dom_sha256, outline, redact_url

#: 网络日志最多记这么多条（防大页面爆内存）
NET_LOG_LIMIT = 50


@dataclass(frozen=True)
class RenderResult:
    dom_outline: str = ""
    dom_sha256: str = ""
    net_log: tuple[dict[str, Any], ...] = ()
    screenshot_sha256: str = ""
    isolation: str = "host"          # host | container
    outcome: str = "ok"              # ok | blocked | error
    reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "dom_sha256": self.dom_sha256,
            "net_log": list(self.net_log),
            "screenshot_sha256": self.screenshot_sha256,
            "isolation": self.isolation,
            "outcome": self.outcome,
            "reason": self.reason,
        }


def _blocked(reason: str, *, isolation: str = "host") -> RenderResult:
    return RenderResult(outcome="blocked", reason=reason, isolation=isolation)


class BrowserRenderer:
    """一个浏览器、多次渲染；**每次渲染一个新 context**。"""

    def __init__(self, *, isolation: str = "host") -> None:
        self.isolation = isolation
        self._playwright: Any = None
        self._browser: Any = None

    # ── 可用性 ─────────────────────────────────────────────────
    def available(self) -> bool:
        try:
            from playwright.sync_api import sync_playwright  # noqa: F401, PLC0415
        except Exception:  # noqa: BLE001
            return False
        return True

    def __enter__(self) -> "BrowserRenderer":
        from playwright.sync_api import sync_playwright  # noqa: PLC0415

        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch()
        return self

    def __exit__(self, *_exc: object) -> None:
        try:
            if self._browser is not None:
                self._browser.close()
        finally:
            if self._playwright is not None:
                self._playwright.stop()
            self._browser = None
            self._playwright = None

    # ── 渲染 ───────────────────────────────────────────────────
    def render(self, *, url: str | None = None, html: str | None = None,
               wait: str = "domcontentloaded", timeout_s: int = 30) -> RenderResult:
        if not self.available():
            return _blocked("renderer_unavailable", isolation=self.isolation)
        if self._browser is None:
            return _blocked("renderer_not_started", isolation=self.isolation)
        if not url and html is None:
            return _blocked("no_input", isolation=self.isolation)

        net: list[dict[str, Any]] = []
        context = self._browser.new_context()          # 关键：每次新 context
        try:
            page = context.new_page()
            page.set_default_timeout(timeout_s * 1000)

            def _on_request(request: Any) -> None:
                if len(net) < NET_LOG_LIMIT:
                    net.append({"method": request.method, "ref": redact_url(request.url)})

            page.on("request", _on_request)
            if html is not None:
                page.set_content(html)
            else:
                page.goto(url or "", wait_until=wait)
            page.wait_for_load_state(wait)

            content = page.content()
            text = outline(content)
            shot = hashlib.sha256(page.screenshot(full_page=True)).hexdigest()
            return RenderResult(dom_outline=text, dom_sha256=dom_sha256(text),
                                net_log=tuple(net), screenshot_sha256=shot,
                                isolation=self.isolation)
        except Exception as exc:  # noqa: BLE001 - 超时/崩溃都归一，**不降级成"没发现"**
            name = type(exc).__name__
            reason = "render_timeout" if "Timeout" in name else f"render_error:{name}"
            return RenderResult(outcome="blocked", reason=reason,
                                net_log=tuple(net), isolation=self.isolation)
        finally:
            context.close()


__all__ = ["BrowserRenderer", "NET_LOG_LIMIT", "RenderResult"]
