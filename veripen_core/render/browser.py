"""浏览器渲染执行体（B46）：把"目标页渲染成可比对的东西"。

边界（`H02 §3.1`）：

- **只渲染**：DOM 大纲 + 脱敏网络日志 + 截图摘要；**永不产 verdict**；
- **每次新 context**：不共享 cookie/localStorage，避免"上一个动作的登录态"偷偷影响这一次；
- **超时即 BLOCKED**：渲染失败不退化成"没发现"；
- **宿主降级是显式的**：本机没有容器，`isolation="host"` 必须被调用方与证据同时看见。
- **浏览器通道也是显式的**：自带 chromium 起不来时回退到系统 Edge/Chrome，
  实际用哪个写进 `RenderResult.channel`（证据里不能自称用了 chromium）。

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
    #: 实际用的浏览器通道：``chromium`` / ``msedge`` / ``chrome`` / 空（没起来）
    channel: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "dom_sha256": self.dom_sha256,
            "net_log": list(self.net_log),
            "screenshot_sha256": self.screenshot_sha256,
            "isolation": self.isolation,
            "outcome": self.outcome,
            "reason": self.reason,
            "channel": self.channel,
        }


def _blocked(reason: str, *, isolation: str = "host") -> RenderResult:
    return RenderResult(outcome="blocked", reason=reason, isolation=isolation)


#: 原始响应体只留这么多：判定只需要"载荷有没有原样出现"。
RAW_LIMIT = 256 * 1024


@dataclass(frozen=True)
class CaptureResult:
    """渲染一页的结果，供**发现**用（不是证据）。

    ``html`` 是渲染后的 DOM 源码；``paths`` 是同源请求路径（已去掉 host）。
    地址不进这里——host 由调用方的绑定决定（N2）。
    """

    html: str = ""
    paths: tuple[str, ...] = ()
    outcome: str = "ok"
    reason: str = ""
    channel: str = ""
    #: ``marker`` 表达式的求值结果（空串 = 没有标记 / 没取到）。
    #: 判定 DOM XSS 只用这个信号：**只有真执行才会写值**。
    marker: str = ""
    #: 网络原始响应体（**未经脚本加工**）。用来区分"服务端回显"与"客户端拼接"。
    #: 只保留长度上限内的前缀，判定只需要"载荷是否原样出现"。
    raw_html: str = ""


def _same_origin_path(url: str, origin: str) -> str | None:
    """同源 → 返回 path（带查询参数名）；跨域/无法解析 → None。"""
    from urllib.parse import urlsplit

    parts = urlsplit(url)
    if not parts.scheme or not parts.netloc:
        return None
    if f"{parts.scheme}://{parts.netloc}" != origin:
        return None
    names = sorted({kv.split("=", 1)[0] for kv in parts.query.split("&") if kv})
    path = parts.path or chr(0x2F)
    return path + (("?" + "&".join(names)) if names else "")


class BrowserRenderer:
    """一个浏览器、多次渲染；**每次渲染一个新 context**。"""

    #: 启动通道回退链：空串 = 自带 chromium；其余是 playwright 的 channel 名。
    DEFAULT_CHANNELS: tuple[str, ...] = ("", "msedge", "chrome")

    def __init__(
        self,
        *,
        isolation: str = "host",
        channels: tuple[str, ...] | None = None,
    ) -> None:
        self.isolation = isolation
        self.channels = channels or self.DEFAULT_CHANNELS
        self.channel = ""
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
        errors: list[str] = []
        for channel in self.channels:
            try:
                if channel:
                    self._browser = self._playwright.chromium.launch(channel=channel)
                else:
                    self._browser = self._playwright.chromium.launch()
            except Exception as exc:  # noqa: BLE001 - 起不来就试下一个通道
                errors.append(f"{channel or 'bundled'}: {type(exc).__name__}")
                continue
            self.channel = channel or "chromium"
            return self
        # 全部起不来：如实抛，不在"没有浏览器"的情况下假装渲染过
        raise RuntimeError("没有可用的浏览器通道（" + "；".join(errors) + "）")

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
    def capture(
        self,
        *,
        url: str,
        wait: str = "networkidle",
        timeout_s: int = 30,
        marker: str = "",
    ) -> CaptureResult:
        """渲染一页，返回渲染后 HTML + 同源请求路径。**只用于发现，不进证据。**

        为什么不能用 ``render()``：那返回的是 DOM 大纲（去属性、脱敏），
        发现需要的是"页面上真有哪些入口"，包括 JS 生成的链接与接口。
        """
        if not self.available():
            return CaptureResult(outcome="blocked", reason="renderer_unavailable")
        if self._browser is None:
            return CaptureResult(outcome="blocked", reason="renderer_not_started")

        from urllib.parse import urlsplit

        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        seen: list[str] = []

        context = self._browser.new_context()
        try:
            page = context.new_page()
            page.set_default_timeout(timeout_s * 1000)

            def _on_request(request: Any) -> None:
                path = _same_origin_path(request.url, origin)
                if path and path not in seen:
                    seen.append(path)

            page.on("request", _on_request)
            raw = ""
            try:
                response = page.goto(url, wait_until=wait)
                if response is not None:
                    raw = response.text()[:RAW_LIMIT]
            except (NameError, AttributeError, TypeError):
                # 代码错误不许被当成"取不到"：那会让整批判定静默变味（本批踩过）
                raise
            except Exception:  # noqa: BLE001 - 真·网络/渲染异常才降级
                raw = ""
            # 标记位必须在页面脚本跑完之后取：它是"执行过"的唯一证据。
            value = ""
            if marker:
                # onload/onerror 这类回调是异步的：goto 返回时它可能还没跑。
                # 不 settle 就会把"执行了"读成"没执行"（假阴性，B119 踩过）。
                page.wait_for_timeout(200)
                try:
                    probed = page.evaluate(marker)
                    value = "" if probed is None else str(probed)
                except Exception:  # noqa: BLE001 - 取不到就当没有，不猜
                    value = ""
            html = page.content()
            return CaptureResult(
                html=html, paths=tuple(seen), channel=self.channel,
                marker=value, raw_html=raw,
            )
        except Exception as exc:  # noqa: BLE001
            name = type(exc).__name__
            reason = "render_timeout" if "Timeout" in name else f"render_error:{name}"
            return CaptureResult(
                paths=tuple(seen), outcome="blocked", reason=reason, channel=self.channel
            )
        finally:
            context.close()

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
                                isolation=self.isolation, channel=self.channel)
        except Exception as exc:  # noqa: BLE001 - 超时/崩溃都归一，**不降级成"没发现"**
            name = type(exc).__name__
            reason = "render_timeout" if "Timeout" in name else f"render_error:{name}"
            return RenderResult(outcome="blocked", reason=reason,
                                net_log=tuple(net), isolation=self.isolation,
                                channel=self.channel)
        finally:
            context.close()


__all__ = ["BrowserRenderer", "CaptureResult", "NET_LOG_LIMIT", "RenderResult"]
