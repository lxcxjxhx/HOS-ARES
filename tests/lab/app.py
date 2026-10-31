"""本地确定性靶场（B5）：一个可注入缺陷的 WSGI 应用，作 Transport 的对端。

设计纪律：
- **不联网**：``WsgiTransport`` 直接调 WSGI app，不碰 socket；``ping -n 1 example.invalid``
  都发不出去也照样跑。
- **有/无缺陷成对**：同一份代码用 ``secure=True/False`` 切换"安全实现/脆弱实现"，
  正样本取自脆弱态、负样本取自安全态——这是 FP=0 的最低要求（10 §3）。
- **路由文本只在本文件**：``assets.json`` 声明模板，只有靶场与测试夹具允许出现路径字面量。

靶场端点（RW 语义由 profile 顶棚约束）：

| 模板 | 语义标签 | 说明 |
|---|---|---|
| ``/api/items/{id}`` | object_read | 对象读，**对象级授权**由 ``secure`` 决定 |
| ``/api/search?q=`` | param_injectable | 受控查询参数，布尔注入由 ``secure`` 决定是否可进查询 |
| ``/api/fetch?url=`` | url_fetch | 服务端取数，SSRF 由 ``secure`` 决定是否限域 |
| ``/api/read?name=`` | file_read | 文件读，供 W2 的 path_traversal 用 |
| ``/api/login`` | token_issue | 发 JWT，``alg`` 由 ``secure`` 决定是否强校验 |
| ``/api/me`` | protected_read | 受保护读，``alg=none`` 是否被接受由 ``secure`` 决定 |
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping
from urllib.parse import parse_qs

ITEMS = {
    1: {"id": 1, "owner": "primary", "secret": "alpha-floor-plan"},
    2: {"id": 2, "owner": "secondary", "secret": "beta-floor-plan"},
    3: {"id": 3, "owner": "primary", "secret": "gamma-floor-plan"},
    # 4 号对象归 admin：任何非 admin 都读不到——这是"系统确实有对象级鉴权"的证据锚点
    4: {"id": 4, "owner": "admin", "secret": "root-credentials"},
}

#: 布尔注入的"真/假"响应差异（不是错误页，是**数据差异**——这才叫布尔差分）
SEARCH_ROWS = {
    "true": [{"id": 1, "name": "widget"}, {"id": 2, "name": "gadget"}, {"id": 3, "name": "sprocket"}],
    "false": [],
}

FILES = {"manual.txt": "install guide v1", "readme.txt": "hello"}

JWT_SECRET = b"lab-hs256-secret"
ADMIN_OWNER = "admin"
#: 靶场的两个"域"符号：允许取数的文档域，与用于外带证明的 OOB 前缀
ALLOWED_FETCH_PREFIX = "doc-SAMPLE"
OOB_PREFIX = "oob-SAMPLE"


# ── JWT 最小实现（HS256 / none）本地靶场用，便于制造与验证变体 ──────
def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64(text: str) -> bytes:
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + pad)


def jwt_encode(payload: dict[str, Any], *, alg: str = "HS256", secret: bytes = JWT_SECRET) -> str:
    header = {"alg": alg, "typ": "JWT"}
    body = f"{_b64(json.dumps(header).encode())}.{_b64(json.dumps(payload).encode())}"
    if alg == "none":
        return body + "."
    return body + "." + _b64(hmac.new(secret, body.encode(), hashlib.sha256).digest())


def jwt_decode(token: str, *, accept_none: bool, secret: bytes = JWT_SECRET) -> dict[str, Any] | None:
    parts = token.split(".")
    if len(parts) != 3:
        return None
    header = json.loads(_unb64(parts[0]))
    alg = header.get("alg")
    if alg == "none":
        if not accept_none:
            return None
    else:
        expect = hmac.new(secret, f"{parts[0]}.{parts[1]}".encode(), hashlib.sha256).digest()
        try:
            got = _unb64(parts[2])
        except Exception:
            return None
        if not hmac.compare_digest(expect, got):
            return None
    return json.loads(_unb64(parts[1]))


def make_token(account: str) -> str:
    return jwt_encode({"sub": account, "role": "admin" if account == "primary" else "user"})


@dataclass
class App:
    """脆弱态（secure=False）/ 安全态（secure=True）/ 退化态（degenerate=True）。

    ``degenerate`` 是**误报之源**的写照：所有端点都回同一个 200 页面，
    参数一律不生效。扫描器在这里最容易报出"到处是洞"，而我们的三条件必须把它压死。
    """

    secure: bool = False
    degenerate: bool = False
    #: OOB 回调登记处：靶场的 fetch 端点在"愿意发请求"时把 nonce 写进来
    callbacks: list[dict[str, str]] = field(default_factory=list)
    log: list[dict[str, Any]] = field(default_factory=list)

    # ── WSGI ────────────────────────────────────────────────────
    def __call__(self, environ: Mapping[str, Any], start_response: Callable[..., Any]) -> list[bytes]:
        method = environ.get("REQUEST_METHOD", "GET")
        path = environ.get("PATH_INFO", "")
        query = parse_qs(environ.get("QUERY_STRING", ""))
        token = _bearer(environ)
        self.log.append({"method": method, "path": path, "query": query, "token": bool(token)})

        if self.degenerate:
            status, ctype, body = 200, "application/json", {"ok": True}
        else:
            status, ctype, body = self._route(method, path, query, token)
        payload = body if isinstance(body, bytes) else json.dumps(body).encode()
        start_response(f"{status} X", [("Content-Type", ctype), ("Content-Length", str(len(payload)))])
        return [payload]

    # ── 路由 ────────────────────────────────────────────────────
    def _route(
        self, method: str, path: str, query: Mapping[str, list[str]], token: str | None
    ) -> tuple[int, str, Any]:
        if m := re.fullmatch(r"/api/items/(\d+)", path):
            return self._item(int(m.group(1)), token)
        if path == "/api/search":
            return self._search(query.get("q", [""])[0])
        if path == "/api/fetch":
            return self._fetch(query.get("url", [""])[0])
        if path == "/api/read":
            return self._read(query.get("name", [""])[0])
        if path == "/api/login":
            return self._login(query.get("account", [""])[0])
        if path == "/api/me":
            return self._me(token)
        return 404, "application/json", {"error": "not_found"}

    def _item(self, item_id: int, token: str | None) -> tuple[int, str, Any]:
        claims = self._claims(token)
        if claims is None:
            return 401, "application/json", {"error": "unauthenticated"}
        item = ITEMS.get(item_id)
        if item is None:
            return 404, "application/json", {"error": "not_found"}
        # admin 的对象**永远**不可越权读：两种模式下都 403。
        # 这是 IDOR 的对照锚点——证明"系统有对象级鉴权，只是漏了普通对象"。
        if item["owner"] == ADMIN_OWNER and claims["sub"] != ADMIN_OWNER:
            return 403, "application/json", {"error": "forbidden"}
        # 对象级授权开关：secure=True 才校验归属；False 就是 IDOR
        if self.secure and item["owner"] != claims["sub"]:
            return 403, "application/json", {"error": "forbidden"}
        return 200, "application/json", item

    def _search(self, q: str) -> tuple[int, str, Any]:
        # 脆弱态：q 直接拼进"查询"；安全态：参数化，永远返回同样的 3 行
        if self.secure:
            return 200, "application/json", {"rows": SEARCH_ROWS["true"], "q": q}
        if re.search(r"1\s*=\s*1", q) and not re.search(r"1\s*=\s*2", q):
            return 200, "application/json", {"rows": SEARCH_ROWS["true"]}
        if re.search(r"1\s*=\s*2", q):
            return 200, "application/json", {"rows": SEARCH_ROWS["false"]}
        return 200, "application/json", {"rows": SEARCH_ROWS["true"]}

    def _fetch(self, target: str) -> tuple[int, str, Any]:
        host = target.split("://", 1)[0] if "://" in target else target
        # 安全态：只允许业务许可的文档域；脆弱态：任何主机都去连
        if self.secure and not target.startswith(ALLOWED_FETCH_PREFIX):
            return 400, "application/json", {"error": "url_not_allowed"}
        if target.startswith(OOB_PREFIX):
            self.callbacks.append({"url": target, "ua": "veripen-lab/1.0"})
            return 200, "application/json", {"fetched": True, "bytes": 128}
        return 200, "application/json", {"fetched": True, "bytes": 128, "scheme": host}

    def _read(self, name: str) -> tuple[int, str, Any]:
        if self.secure and (".." in name or name.startswith(chr(0x2F))):
            return 400, "application/json", {"error": "bad_name"}
        if name in FILES:
            return 200, "text/plain", FILES[name]
        return 404, "text/plain", "not found"

    def _login(self, account: str) -> tuple[int, str, Any]:
        if account not in ("primary", "secondary"):
            return 400, "application/json", {"error": "unknown_account"}
        return 200, "application/json", {"token": make_token(account)}

    def _me(self, token: str | None) -> tuple[int, str, Any]:
        claims = self._claims(token)
        if claims is None:
            return 401, "application/json", {"error": "unauthenticated"}
        return 200, "application/json", {"sub": claims["sub"], "role": claims.get("role")}

    def _claims(self, token: str | None) -> dict[str, Any] | None:
        if not token:
            return None
        return jwt_decode(token, accept_none=not self.secure)


def _bearer(environ: Mapping[str, Any]) -> str | None:
    raw = environ.get("HTTP_AUTHORIZATION", "")
    if raw.lower().startswith("bearer "):
        return raw[7:]
    return None


# ── 资产声明（模板 → 语义标签）──────────────────────────────────────
def assets_json() -> dict[str, Any]:
    """靶场对外发布的机器可读声明；discovery 只认标签，不认路径（27 §3.1）。"""
    return {
        "asset_id": "web-01",
        "resolved_ip": "203.0.113.10",
        "operations": [
            {"method": "GET", "template": "/api/items/{id}", "tags": ["object_read"],
             "params": [{"name": "id", "kind": "int"}]},
            {"method": "GET", "template": "/api/search", "tags": ["param_injectable"],
             "params": [{"name": "q", "kind": "str"}]},
            {"method": "GET", "template": "/api/fetch", "tags": ["url_fetch"],
             "params": [{"name": "url", "kind": "url"}]},
            {"method": "GET", "template": "/api/read", "tags": ["file_read"],
             "params": [{"name": "name", "kind": "str"}]},
            {"method": "POST", "template": "/api/login", "tags": ["token_issue"],
             "params": [{"name": "account", "kind": "str"}]},
            {"method": "GET", "template": "/api/me", "tags": ["protected_read"],
             "params": []},
        ],
    }


def new_id() -> str:
    return uuid.uuid4().hex
