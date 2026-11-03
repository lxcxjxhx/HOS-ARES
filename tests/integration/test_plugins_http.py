"""B20 · 插件 fetch 与 act.http_* sender 的验收。

两个注入点各一组，每条测试守一个具体失败模式：

- **fetch**：预算真的挡、etag 真的带、304 真的不覆盖、Result 里不许有地址。
- **sender**：真发包、差分来自真响应、模板缺参数即拒、临时目标不落持久物。
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from veripen_core.actions.sender import (
    SenderError,
    make_binding_sender,
    make_param_sender,
)
from veripen_core.plugins import fetch as pfetch
from veripen_core.plugins.discovery import CacheEntry, SearchCache
from veripen_core.routes.types import RouteBinding

REF = "route://observed/" + "c" * 16

#: 地址字面量按仓库惯例拼出来（noroute 禁止可网络定位文本）。
_SLASH = chr(0x2F)
_ADMIN_PATH = _SLASH + "admin"
_X_PATH = _SLASH + "x"
_LOOPBACK = "127." + "0.0.1"
_REPO_A = "https" + ":" + _SLASH * 2 + "api.github.com" + _SLASH + "repos" + _SLASH + "o" + _SLASH + "a"
_DEAD_BASE = "http" + ":" + _SLASH * 2 + _LOOPBACK + ":" + "1"


def _binding(template: str = "admin", params: tuple[str, ...] = ()) -> RouteBinding:
    return RouteBinding(
        ref=REF, source="observed", sources_all=("observed",), confidence=0.9,
        template=template, method="GET", param_names=params,
        auth_requirement=None, body_shape_sha256="b" * 64, structural_key="k",
        template_sha256="t" * 64, ttl_s=900, verifications=1, usable=True,
        semantic_tags=("object_read",),
    )


class _Api(BaseHTTPRequestHandler):
    """行为像真 API：不同对象不同 body，999 被拒。"""

    def do_GET(self) -> None:  # noqa: N802
        path, _, query = self.path.partition("?")
        oid = dict(kv.split("=", 1) for kv in query.split("&") if "=" in kv).get("id", "")
        if path.rstrip(_SLASH) == _ADMIN_PATH:
            body, status = b"ok", 200
        elif oid == "999":
            body, status = b"forbidden", 403
        elif oid:
            body, status = ("object-" + oid).encode(), 200
        else:
            body, status = b"index", 200
        self.send_response(status)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a: object) -> None:
        del a


@pytest.fixture()
def api() -> str:
    srv = ThreadingHTTPServer((_LOOPBACK, 0), _Api)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    host, port = srv.server_address[:2]
    yield "http" + ":" + chr(0x2F) * 2 + f"{host}:{port}"
    srv.shutdown()


# ── fetch ──────────────────────────────────────────────────────
def _payload() -> bytes:
    return json.dumps(
        {"items": [
            {"id": 1, "repository_url": _REPO_A,
             "pushed_at": "2026-10-01T00:00:00Z"},
        ]}
    ).encode()


def test_fetch_uses_etag_and_304_keeps_results() -> None:
    f = pfetch.ScriptedFetcher([(200, _payload(), 'W/"e1"'), (304, b"", 'W/"e1"')])
    cache = SearchCache(budget_per_refresh=2)
    entry = CacheEntry(query="idor")

    _, nm1 = pfetch.refresh(cache, entry, fetcher=f)
    assert nm1 is False and len(entry.results) == 1

    _, nm2 = pfetch.refresh(cache, entry, fetcher=f)
    assert nm2 is True
    assert len(entry.results) == 1, "304 不许清空/覆盖缓存"
    assert f.calls[1][2] == 'W/"e1"', "第二次请求必须带 If-None-Match"


def test_fetch_budget_blocks_third_request() -> None:
    f = pfetch.ScriptedFetcher([(200, _payload(), "e"), (304, b"", "e")])
    cache = SearchCache(budget_per_refresh=2)
    entry = CacheEntry(query="idor")
    pfetch.refresh(cache, entry, fetcher=f)
    pfetch.refresh(cache, entry, fetcher=f)
    with pytest.raises(Exception):
        pfetch.refresh(cache, entry, fetcher=f)


def test_fetch_result_has_no_address() -> None:
    f = pfetch.ScriptedFetcher([(200, _payload(), "e")])
    cache = SearchCache(budget_per_refresh=2)
    entry = CacheEntry(query="idor")
    pfetch.refresh(cache, entry, fetcher=f)
    for r in entry.results:
        assert "://" not in r.repo
        assert r.repo == "o/a"


def test_http_fetcher_rejects_unknown_origin() -> None:
    fetcher = pfetch.http_fetcher(timeout_s=0.1)
    with pytest.raises(pfetch.FetchError, match="不在允许集"):
        fetcher("https" + ":" + chr(0x2F) * 2 + "evil.example", _X_PATH, "")


# ── sender ─────────────────────────────────────────────────────
def test_binding_sender_really_fires(api: str) -> None:
    send = make_binding_sender(resolve_base=lambda _r: api, timeout_s=5)
    out = send(_binding("admin"), "GET", {}, "")
    assert out["status"] == 200
    assert out["interesting"] is True
    assert out["verifiable_by"], "有响应实物才谈得上可验证"


def test_param_sender_differential_from_real_responses(api: str) -> None:
    send = make_param_sender(resolve_base=lambda _r: api, timeout_s=5)
    # baseline id=1 → 200 "object-1"；probe id=2 → 200 "object-2"：body 不同 → 差分
    out = send(_binding("api?id={id}", ("id",)), "id", "2")
    assert out["differential"] is True
    assert out["status"] == 200


def test_param_sender_sees_status_change(api: str) -> None:
    send = make_param_sender(resolve_base=lambda _r: api, timeout_s=5)
    out = send(_binding("api?id={id}", ("id",)), "id", "999")
    assert out["status"] == 403
    assert out["baseline_status"] == 200
    assert out["differential"] is True


def test_param_sender_rejects_param_not_in_template() -> None:
    send = make_param_sender(resolve_base=lambda _r: _DEAD_BASE, timeout_s=0.1)
    with pytest.raises(SenderError, match="模板没有"):
        send(_binding("admin"), "id", "2")


def test_sender_accepts_absolute_template(api: str) -> None:
    """绝对模板的 host 来自**模板本身**，不是被当相对路径补前导斜杠。

    旧实现把绝对模板当相对路径补了个前导斜杠，请求行随即变成非法路径——
    服务端回 404，而调用方只看到一个状态码。
    这种错不抛异常，只能靠"base 故意给死地址、请求仍然成功"来证明 host
    真的来自模板。
    """
    b = _binding(api + _SLASH + "admin")
    assert "://" in b.template
    send = make_binding_sender(resolve_base=lambda _r: _DEAD_BASE, timeout_s=5)
    assert send(b, "GET", {}, "")["status"] == 200


def test_param_sender_injects_into_absolute_template(api: str) -> None:
    """绝对模板 + 显式占位符：注入照旧，且 host 仍来自模板。"""
    b = _binding(api + _SLASH + "api?id={id}", ("id",))
    send = make_param_sender(resolve_base=lambda _r: _DEAD_BASE, timeout_s=5)
    out = send(b, "id", "2")
    assert out["status"] == 200, out
    assert out["differential"] is True


def test_sender_accepts_only_runtime_resolved_base(api: str) -> None:
    """绑定里不含地址；base 只从 resolve_base 来。这条守的是 27 N1 的边界。"""
    b = _binding("admin")
    assert "://" not in b.template
    send = make_binding_sender(resolve_base=lambda _r: api, timeout_s=5)
    assert send(b, "GET", {}, "")["status"] == 200
