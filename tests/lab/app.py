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

#: "系统文件"内容：命中指纹表即证明真的读到了系统文件（不是 200 就算）
SYSTEM_FILES = {
    "etc/passwd": "root:x:0:0:root:/root:/bin/sh\ndaemon:x:1:1:daemon:/usr/sbin:/bin/false\n",
    "windows/win.ini": "[fonts]\n[extensions]\nfor 16-bit app support\n",
}

#: 站内跳转目标（相对路径）：基线用它证明"跳转功能本身是好的"
SAFE_REDIRECT_TARGET = "account/settings"

JWT_SECRET = b"lab-hs256-secret"
ADMIN_OWNER = "admin"
REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
ALLOWED_ORIGIN = "site.invalid"
ADMIN_ROLE = "admin"
#: 安全态的限流阈值：超过就 429（脆弱态不计数）
RATE_LIMIT_HITS = 5
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


def jwt_decode(
    token: str,
    *,
    accept_none: bool,
    check_time: bool = True,
    verify_signature: bool = True,
    secret: bytes = JWT_SECRET,
) -> dict[str, Any] | None:
    parts = token.split(".")
    if len(parts) != 3:
        return None
    header = json.loads(_unb64(parts[0]))
    alg = header.get("alg")
    if not verify_signature and not sig_looks_plausible(parts[2]):
        # 脆弱实现也会做的最低限度检查：签名段必须"看起来像签名"。
        # 这条让 control（无效签名字节）仍然被拒，同时 J2/J3 的"改声明保签名"仍能通过。
        return None
    if alg == "none":
        if not accept_none:
            return None
    elif verify_signature:
        expect = hmac.new(secret, f"{parts[0]}.{parts[1]}".encode(), hashlib.sha256).digest()
        try:
            got = _unb64(parts[2])
        except Exception:
            return None
        if not hmac.compare_digest(expect, got):
            return None
    claims = dict(json.loads(_unb64(parts[1])))
    if check_time:
        now = 1_800_000_000  # 靶场固定"当前时刻"，不读真实时钟（判定必须可复现）
        if isinstance(claims.get("exp"), (int, float)) and claims["exp"] < now:
            return None
        if isinstance(claims.get("nbf"), (int, float)) and claims["nbf"] > now:
            return None
    return claims


def sig_looks_plausible(sig: str) -> bool:
    """最低限度的"像不像签名"检查：长度够、只含 base64url 字符。

    脆弱实现只做到这一步（不验内容），这正是 J2–J9 能成立的原因；
    control 用"签名段被破坏"来制造无效，因此仍会被拒——这一步必须是公道的，
    否则 control 也放行，就变成"整站没验签"，判不出具体缺陷。
    """
    if len(sig) < 32:
        return False
    return all(c.isalnum() or c in "-_" for c in sig)


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
    #: 账号可写状态（display_name / role / is_admin）+ 限流计数
    state: dict[str, Any] = field(
        default_factory=lambda: {
            "primary": {"sub": "primary", "display_name": "p", "role": "user"},
            "secondary": {"sub": "secondary", "display_name": "s", "role": "user"},
            "rate_hits": 0,
        }
    )

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
            status, ctype, body = self._route(method, path, query, token, environ)
        payload = body if isinstance(body, bytes) else (
            body.encode() if isinstance(body, str) else json.dumps(body).encode()
        )
        headers = [("Content-Type", ctype), ("Content-Length", str(len(payload)))]
        origin = environ.get("HTTP_ORIGIN")
        if origin:
            # 脆弱态：任何 Origin 都回显（且带 credentials）；安全态：只允许站内
            if not self.secure:
                headers.append(("Access-Control-Allow-Origin", str(origin)))
                headers.append(("Access-Control-Allow-Credentials", "true"))
            else:
                headers.append(("Access-Control-Allow-Origin", ALLOWED_ORIGIN))
                headers.append(("Vary", "Origin"))
        if status in REDIRECT_STATUSES:
            headers.append(("Location", query.get("to", [""])[0]))
        start_response(f"{status} X", headers)
        return [payload]

    # ── 路由 ────────────────────────────────────────────────────
    def _route(
        self,
        method: str,
        path: str,
        query: Mapping[str, list[str]],
        token: str | None,
        environ: Mapping[str, Any],
    ) -> tuple[int, str, Any]:
        if path == "/api/items":
            return 200, "application/json", {"items": list(ITEMS.values()), "total": len(ITEMS)}
        if m := re.fullmatch(r"/api/items/(\d+)", path):
            return self._item(int(m.group(1)), token)
        if path == "/api/search":
            maybe = self._maybe_sql_error(query.get("q", [""])[0])
            if maybe is not None:
                return maybe
            return self._search(query.get("q", [""])[0])
        if path == "/api/fetch":
            return self._fetch(query.get("url", [""])[0])
        if path == "/api/read":
            return self._read(query.get("name", [""])[0])
        if path == "/api/login":
            return self._login(query.get("account", [""])[0])
        if path == "/api/me":
            return self._me(token)
        if path == "/api/redirect":
            return self._redirect(query.get("to", [""])[0])
        if path == "/api/admin/audit":
            return self._admin(token)
        if path == "/api/profile/normal":
            return self._normal(token)
        if path == "/api/echo":
            return self._echo(query.get("q", [""])[0])
        if path == "/api/profile":
            return self._profile_write(query, token)
        if path == "/api/profile/me":
            return self._profile_read(token)
        if path == "/api/ping":
            return self._ping()
        if path == "/api/notes":
            return self._notes_write(query, token)
        if path == "/api/notes/all":
            return self._notes_read()
        if path == "/api/graphql":
            return self._graphql(_headers_of(environ))
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

    def _maybe_sql_error(self, q: str) -> tuple[int, str, Any] | None:
        """脆弱态：不平衡引号 → 数据库错误页。安全态永远不进这里。"""
        if self.secure:
            return None
        if q.count("'") % 2 == 1 or q.count('"') % 2 == 1:
            return 500, "text/html", "<h1>500</h1><pre>SQL syntax error near " + q[:20] + "</pre>"
        return None

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
        # 脆弱态：拼接后直接读 —— 越界即读到系统文件；越界到不存在的文件仍是 404
        for suffix, content in SYSTEM_FILES.items():
            if name.endswith(suffix):
                return 200, "text/plain", content
        return 404, "text/plain", "not found"

    def _admin(self, token: str | None) -> tuple[int, str, Any]:
        claims = self._claims(token)
        if claims is None:
            return 401, "application/json", {"error": "unauthenticated"}
        # 脆弱态：只校验"登录了"，不校验角色 —— 功能级越权
        if self.secure and claims.get("role") != ADMIN_ROLE:
            return 403, "application/json", {"error": "forbidden"}
        return 200, "application/json", {"admin": True, "rows": [{"audit": "login"}]}

    def _normal(self, token: str | None) -> tuple[int, str, Any]:
        claims = self._claims(token)
        if claims is None:
            return 401, "application/json", {"error": "unauthenticated"}
        return 200, "application/json", {"ok": True, "sub": claims["sub"]}

    def _echo(self, q: str) -> tuple[int, str, Any]:
        """回显端点：安全态转义尖引号与引号，脆弱态原样拼进 HTML。"""
        if self.secure:
            safe = (
                q.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                .replace('"', "&quot;").replace("'", "&#39;")
            )
            return 200, "text/html", f"<div class=\"q\">{safe}</div>"
        return 200, "text/html", f"<div class=\"q\">{q}</div>"

    def _profile_write(self, query: Mapping[str, list[str]], token: str | None) -> tuple[int, str, Any]:
        claims = self._claims(token)
        if claims is None:
            return 401, "application/json", {"error": "unauthenticated"}
        raw = query.get("display_name", [""])[0]
        # 脆弱态：把整行 query 当成"对象更新"直接合并（含只读字段）
        if not self.secure:
            for key, values in query.items():
                if key in ("role", "is_admin") and values:
                    self.state[claims["sub"]][key] = values[0]
        if raw:
            self.state[claims["sub"]]["display_name"] = raw
        return 200, "application/json", {"ok": True}

    def _profile_read(self, token: str | None) -> tuple[int, str, Any]:
        claims = self._claims(token)
        if claims is None:
            return 401, "application/json", {"error": "unauthenticated"}
        return 200, "application/json", dict(self.state[claims["sub"]])

    def _ping(self) -> tuple[int, str, Any]:
        if self.secure:
            self.state["rate_hits"] += 1
            if self.state["rate_hits"] > RATE_LIMIT_HITS:
                return 429, "application/json", {"error": "rate_limited"}
        return 200, "application/json", {"ok": True}

    def _notes_write(self, query: Mapping[str, list[str]], token: str | None) -> tuple[int, str, Any]:
        """内容写入。脆弱态：原样存储；安全态：实体化后再存。"""
        claims = self._claims(token)
        if claims is None:
            return 401, "application/json", {"error": "unauthenticated"}
        body = query.get("body", [""])[0]
        stored = body if not self.secure else (
            body.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;").replace("'", "&#39;")
        )
        self.state.setdefault("notes", []).append({"author": claims["sub"], "body": stored})
        return 201, "application/json", {"ok": True, "stored": len(stored)}

    def _notes_read(self) -> tuple[int, str, Any]:
        notes = self.state.get("notes") or [{"author": "seed", "body": "seed content"}]
        rows = "".join(f"<li>{n['body']}</li>" for n in notes)
        return 200, "text/html", f"<ul>{rows}</ul>"

    def _graphql(self, headers: Mapping[str, str]) -> tuple[int, str, Any]:
        raw = headers.get("x-vp-gql", "")
        try:
            payload = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            payload = {}
        query = str(payload.get("query") or "")
        if "__schema" in query:
            if self.secure:
                return 200, "application/json", {"errors": [{"message": "introspection disabled"}]}
            return 200, "application/json", {"data": {"__schema": {"types": [{"name": "User"}]}}}
        if query.startswith("mutation"):
            if self.secure:
                return 200, "application/json", {"errors": [{"message": "forbidden"}]}
            return 200, "application/json", {"data": {"ok": True}}
        return 200, "application/json", {"data": {"__typename": "Query"}}

    def _redirect(self, target: str) -> tuple[int, str, Any]:
        """跳转端点。安全态：只接受相对路径；脆弱态：原样写进 Location。"""
        if not target:
            return 400, "application/json", {"error": "missing_target"}
        if target.startswith("javascript:") or target.startswith("data:"):
            return 400, "application/json", {"error": "scheme_not_allowed"}
        if self.secure and "://" in target:
            return 400, "application/json", {"error": "external_target_not_allowed"}
        if self.secure and target.startswith("//"):
            return 400, "application/json", {"error": "external_target_not_allowed"}
        return 302, "text/plain", ""

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
        # 脆弱态：不验签、不验 alg、不验 exp/nbf —— 只有 J 系列能证明这一点；
        # 安全态：三者都验。（J7/J8 的"过期仍可用"必须真的走到这里）
        return jwt_decode(
            token,
            accept_none=not self.secure,
            check_time=self.secure,
            # 脆弱态：**根本不进签名校验分支**（verify_signature=False）。
            # 但 control 必须仍被拒——所以 control 靠 alg/结构而不是靠验签：
            # 见 _claims 里的"空签名段一律拒"规则。
            verify_signature=self.secure,
        )


def _headers_of(environ: Mapping[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in environ.items():
        if key.startswith("HTTP_"):
            out[key[5:].lower().replace("_", "-")] = str(value)
    return out


def _bearer(environ: Mapping[str, Any]) -> str | None:
    raw = str(environ.get("HTTP_AUTHORIZATION", "") or "")
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
            {"method": "GET", "template": "/api/redirect", "tags": ["redirect_entry"],
             "params": [{"name": "to", "kind": "url"}]},
            {"method": "GET", "template": "/api/admin/audit", "tags": ["admin_function"],
             "params": []},
            {"method": "GET", "template": "/api/profile/normal", "tags": ["normal_function"],
             "params": []},
            {"method": "GET", "template": "/api/echo", "tags": ["echo_param"],
             "params": [{"name": "q", "kind": "str"}]},
            {"method": "POST", "template": "/api/profile", "tags": ["profile_write"],
             "params": [{"name": "display_name", "kind": "str"}]},
            {"method": "GET", "template": "/api/profile/me", "tags": ["profile_read"],
             "params": []},
            {"method": "GET", "template": "/api/ping", "tags": ["rate_sensitive"],
             "params": []},
            {"method": "POST", "template": "/api/notes", "tags": ["content_write"],
             "params": [{"name": "body", "kind": "str"}]},
            {"method": "GET", "template": "/api/notes/all", "tags": ["content_read"],
             "params": []},
            {"method": "POST", "template": "/api/graphql", "tags": ["graphql_endpoint"],
             "params": []},
            {"method": "GET", "template": "/api/items", "tags": ["api_read"],
             "params": []},
            {"method": "POST", "template": "/api/login", "tags": ["token_issue"],
             "params": [{"name": "account", "kind": "str"}]},
            {"method": "GET", "template": "/api/me", "tags": ["protected_read"],
             "params": []},
        ],
    }


def new_id() -> str:
    return uuid.uuid4().hex
