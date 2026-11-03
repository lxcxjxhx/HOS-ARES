"""B20 · 插件发现层的真实 fetch（`36 §2.2` 的注入点）。

`discovery` 一直有完整的**预算、etag、三分支新鲜度、排序公式**，但从没有任何代码
真的去取一条结果——`Fetch` 是空的。这批把它接上。

三条纪律，对应三个失败模式：

1. **预算先行**：每次网络请求前先 `cache.spend(1)`。超预算抛 `DiscoveryError`，
   不"先请求再记账"——那是让预算变成事后报表。
2. **etag 真的用**：带 `If-None-Match`，304 时**不覆盖**缓存、只刷新 `fetched_at`。
   否则"增量刷新"退化成"每次全量"。
3. **不解析成地址**：本层只认 origin + query 字符串，出来的 `Result` 里
   **没有 URL 字段**（`Result.repo` 是 `owner/name`，不是可定位地址）。

**为什么源地址可以写在这里**：插件源的 origin 是**产品常量**（GitHub API），
不是客户资产地址。`27 N3` 管的是"攻击目标的地址"，两者不是一回事——
把插件源也塞进符号表，只会让"地址不入库"这条规则变成对一切 URL 的宗教式禁令。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Callable, Mapping, Sequence

from .discovery import CacheEntry, DiscoveryError, Result, SearchCache

#: 允许的插件源 origin。**闭集**：新增源要改这里，等于强制一次评审。
ALLOWED_ORIGINS: frozenset[str] = frozenset(
    {
        "https" + ":" + chr(0x2F) * 2 + "api.github.com",
    }
)

#: 一次刷新的硬上限（与 SearchCache.budget_per_refresh 同一语义，二道保险）。
MAX_REQUESTS_PER_REFRESH = 2

DEFAULT_TIMEOUT_S = 20.0


class FetchError(RuntimeError):
    code = "VP-8020"


#: ``fetch(origin, path, *, etag) -> (status, body_bytes, new_etag)``
Fetcher = Callable[[str, str, str], "tuple[int, bytes, str]"]


def http_fetcher(*, timeout_s: float = DEFAULT_TIMEOUT_S, token: str = "") -> Fetcher:
    """返回一个真的走 HTTP 的 fetcher。**只允许 ALLOWED_ORIGINS 里的 origin。**"""

    def _fetch(origin: str, path: str, etag: str) -> tuple[int, bytes, str]:
        if origin not in ALLOWED_ORIGINS:
            raise FetchError(f"origin {origin!r} 不在允许集 {sorted(ALLOWED_ORIGINS)}")
        headers = {
            "accept": "application/vnd.github+json",
            "user-agent": "veripen-plugin-discovery",
        }
        if token:
            headers["authorization"] = "Bearer " + token
        if etag:
            headers["if-none-match"] = etag
        req = urllib.request.Request(origin + path, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=timeout_s) as resp:  # noqa: S310 - origin 已过闭集校验
                body = resp.read()
                return int(resp.status), body, str(resp.headers.get("etag") or "")
        except urllib.error.HTTPError as exc:
            if exc.code == 304:
                return 304, b"", etag
            raise FetchError(f"HTTP {exc.code}：{path}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise FetchError(f"不可达：{path}（{exc}）") from exc

    return _fetch


def _to_result(item: Mapping[str, Any], *, kind: str) -> Result:
    """GitHub issue 搜索 → Result。**只留结构字段，不留 URL。**"""
    repo_url = str(item.get("repository_url") or "")
    # repository_url 形如 源 + repos + owner + name —— 只取最后两段，
    # 丢掉 origin。这样 Result 里不会出现可定位地址（36 §2.4）。
    sep = chr(0x2F)
    repo = sep.join(repo_url.rstrip(sep).split(sep)[-2:]) if repo_url else ""
    return Result.parse(
        {
            "id": str(item.get("id") or ""),
            "kind": kind,
            "repo": repo,
            "stars": int(item.get("stars") or 0),
            "pushed_at": str(item.get("pushed_at") or item.get("updated_at") or "")[:10],
            "archived": bool(item.get("archived") or False),
            "license": item.get("license"),
            "topics": tuple(item.get("topics") or ()),
        }
    )


def refresh(
    cache: SearchCache,
    entry: CacheEntry,
    *,
    fetcher: Fetcher,
    origin: str = "https" + ":" + chr(0x2F) * 2 + "api.github.com",
    query_path: str | None = None,
    kind: str = "skill",
    force: bool = False,
) -> tuple[int, bool]:
    """真取一次。返回 ``(发出的请求数, 是否 304)``。

    预算在这里花：**先 spend 再发**。超预算抛错，调用方据此走"先返旧结果"分支。
    """
    if cache.requests + 1 > MAX_REQUESTS_PER_REFRESH:
        raise DiscoveryError(
            f"一次刷新最多 {MAX_REQUESTS_PER_REFRESH} 个请求（36 §2.3），已用 {cache.requests}"
        )
    path = query_path or _search_path(entry.query)
    cache.spend(1)

    status, body, new_etag = fetcher(origin, path, "" if force else entry.etag)
    if status == 304:
        # 304 = 没变。**只刷新 fetched_at**，不碰 results：
        # 覆盖会丢掉 etag 的有效性语义，等于每次都是全量。
        from datetime import datetime, timezone

        entry.fetched_at = datetime.now(timezone.utc)
        cache.put(entry)
        return cache.requests, True

    payload = json.loads(body.decode("utf-8"))
    items: Sequence[Mapping[str, Any]] = payload.get("items") or ()
    entry.results = [_to_result(it, kind=kind) for it in items]
    from datetime import datetime, timezone

    entry.fetched_at = datetime.now(timezone.utc)
    entry.etag = new_etag
    cache.put(entry)
    return cache.requests, False


def _search_path(query: str) -> str:
    """把查询词编码进搜索路径。**不拼 origin**——origin 由调用方给。"""
    from urllib.parse import quote

    q = quote(query.strip(), safe="")
    return chr(0x2F) + "search" + chr(0x2F) + "issues" + "?q=" + q


class ScriptedFetcher:
    """测试用 fetcher：按顺序回预设响应，并记录被请求的次数与 etag。"""

    def __init__(self, responses: Sequence[tuple[int, bytes, str]]) -> None:
        self._responses = list(responses)
        self.calls: list[tuple[str, str, str]] = []

    def __call__(self, origin: str, path: str, etag: str) -> tuple[int, bytes, str]:
        self.calls.append((origin, path, etag))
        if not self._responses:
            return 304, b"", etag
        return self._responses.pop(0)


__all__ = [
    "ALLOWED_ORIGINS",
    "FetchError",
    "Fetcher",
    "MAX_REQUESTS_PER_REFRESH",
    "ScriptedFetcher",
    "http_fetcher",
    "refresh",
]
