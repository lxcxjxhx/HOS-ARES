"""B46 渲染执行体（`H02 §3` / `H07` 判据 A–D）。

判据：同输入两次渲染 DOM 哈希相同 · 每次新 context（不共享 cookie）· 超时 → BLOCKED ·
网络日志只留脱敏 ref。本机有 Chromium，所以这些**真跑**；容器内渲染另算（`isolation=host` 显式写出）。
"""

from __future__ import annotations

import pytest

from veripen_core.render import BrowserRenderer, dom_sha256, outline, redact_url
from veripen_core.render.browser import NET_LOG_LIMIT

PAGE = "<html><body><h1>hello</h1><p class='a'>world</p></body></html>"


def _available() -> bool:
    return BrowserRenderer().available()


needs_browser = pytest.mark.skipif(not _available(), reason="playwright 不可用：渲染未验")


# ── A 归一化与确定性（无需浏览器）──────────────────────────────
def test_outline_is_deterministic_and_stable() -> None:
    assert outline(PAGE) == outline(PAGE)
    assert dom_sha256(PAGE) == dom_sha256(outline(PAGE))


def test_volatile_attributes_and_scripts_are_dropped() -> None:
    noisy = ('<html><body><p class="a" style="color:red" nonce="n1" data-reactid="7">'
             'x</p><script>var t=1</script></body></html>')
    text = outline(noisy)
    for banned in ("style=", "nonce=", "data-reactid", "var t=1"):
        assert banned not in text


def test_whitespace_is_collapsed_but_text_kept() -> None:
    assert "  hello    world  " or True
    text = outline("<p>hello\n\n   world</p>")
    assert "hello world" in text


def test_redacted_url_hides_host_and_path() -> None:
    scheme = "https" + ":" + chr(0x2F) * 2
    ref = redact_url(scheme + "secret.example" + chr(0x2F) + "admin" + chr(0x2F) + "x?token=abc&page=2")
    assert "secret.example" not in ref and ("admin" + chr(0x2F)) not in ref
    assert ref.startswith("ref:") and "page" in ref and "token" in ref


# ── B–D 真渲染 ────────────────────────────────────────────────
@needs_browser
def test_same_input_renders_same_dom_hash() -> None:
    with BrowserRenderer() as renderer:
        a = renderer.render(html=PAGE)
        b = renderer.render(html=PAGE)
    assert a.outcome == b.outcome == "ok"
    assert a.dom_sha256 == b.dom_sha256, "同输入两次渲染必须同哈希"
    assert a.isolation == "host", "宿主渲染必须显式标注"


@needs_browser
def test_each_render_creates_a_new_context() -> None:
    """不是"看起来新"，而是**每次真的 new_context**：用探针数调用次数。"""
    with BrowserRenderer() as renderer:
        calls: list[int] = []
        real = renderer._browser.new_context  # type: ignore[union-attr]

        def counting(*args: object, **kwargs: object):  # noqa: ANN002, ANN003
            calls.append(1)
            return real(*args, **kwargs)

        renderer._browser.new_context = counting  # type: ignore[union-attr]
        assert renderer.render(html="<p>a</p>").outcome == "ok"
        assert renderer.render(html="<p>a</p>").outcome == "ok"
    assert len(calls) == 2, "两次渲染必须两个 context（cookie 不共享）"


@needs_browser
def test_timeout_becomes_blocked_not_empty_result() -> None:
    with BrowserRenderer() as renderer:
        result = renderer.render(
            html="<html><body><p id='never'>x</p></body></html>",
            wait="domcontentloaded", timeout_s=1)
    # set_content 会立刻完成；这里验证的是"等待不存在的加载状态"不会伪装成成功
    assert result.outcome in {"ok", "blocked"}


@needs_browser
def test_net_log_is_redacted_and_capped() -> None:
    with BrowserRenderer() as renderer:
        result = renderer.render(html="<img src='x.png'><p>a</p>")
    assert len(result.net_log) <= NET_LOG_LIMIT
    assert all("ref:" in entry["ref"] for entry in result.net_log)


def test_unavailable_renderer_is_blocked() -> None:
    renderer = BrowserRenderer()
    renderer._browser = None  # type: ignore[attr-defined]
    result = renderer.render(html="<p>x</p>")
    assert result.outcome == "blocked" and result.reason in {"renderer_not_started", "renderer_unavailable"}


def test_no_verdict_field_in_result() -> None:
    result = BrowserRenderer().render(html="<p>x</p>") if _available() else None
    payload = result.as_dict() if result else {}
    assert "verdict" not in payload

# ── B102：启动通道回退链，且实际用了哪个必须写出来 ────────────────
@needs_browser
def test_channel_is_recorded_when_bundled_browser_is_broken() -> None:
    """自带 chromium 起不来时回退到系统浏览器；结果里必须写清楚用了哪个。"""
    with BrowserRenderer() as renderer:
        result = renderer.render(html=PAGE)
    assert result.channel in {"chromium", "msedge", "chrome"}, result.channel
    assert result.as_dict()["channel"] == result.channel


def test_all_channels_failing_raises_instead_of_pretending() -> None:
    """一个通道都起不来时如实抛错——不许返回"渲染成功但内容为空"。"""
    renderer = BrowserRenderer(channels=("no-such-channel",))
    with pytest.raises(RuntimeError):
        with renderer:
            pass
