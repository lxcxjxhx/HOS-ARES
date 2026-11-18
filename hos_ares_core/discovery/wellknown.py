"""B107：well-known 入口（robots.txt / sitemap.xml）进发现。

静态 CMS 的首页只链一部分栏目，真正的全量目录往往在这两个文件里。它们**是站点自己发布的**，
比爬链接便宜也更全。仍然走同一条纪律：先解析成路径，交给 resolver 出绑定（N2）。

安全边界：sitemap 用 ElementTree 解析，**不展开外部实体**（stdlib 默认行为，
不额外开 ``resolve_entities``）；只取 ``<loc>`` 的 path 部分，host 一律丢掉。
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

SEP = chr(0x2F)

#: 这两个路径本身不是"面"，只是入口清单：不记录、只解析。
ROBOTS = SEP + "robots.txt"
SITEMAP = SEP + "sitemap.xml"
WELL_KNOWN = (ROBOTS, SITEMAP)


def _same_origin_path(url: str) -> str | None:
    """绝对 URL → path（带查询参数名）；相对/畸形 → None。"""
    text = url.strip()
    if "://" not in text:
        return None
    rest = text.split("://", 1)[1]
    path = SEP + rest.split(SEP, 1)[1] if SEP in rest else SEP
    path = path.split("#", 1)[0]
    return path or SEP


def paths_from_robots(text: str) -> tuple[str, ...]:
    """robots.txt → 允许/禁止的路径清单。

    只取 ``Allow`` / ``Disallow`` 的路径部分；``Disallow: /`` 是"全站禁爬"，
    跳过它——我们不是爬虫合规场景，而"全站禁爬"里没有信息。
    """
    out: list[str] = []
    for line in text.splitlines():
        raw = line.split("#", 1)[0].strip()
        if not raw or ":" not in raw:
            continue
        key, _, value = raw.partition(":")
        if key.strip().lower() not in ("allow", "disallow"):
            continue
        path = value.strip()
        if not path or path == SEP or "*" in path:
            continue
        if not path.startswith(SEP):
            path = SEP + path
        if path not in out:
            out.append(path)
    return tuple(out)


def paths_from_sitemap(text: str) -> tuple[str, ...]:
    """sitemap.xml → 同源路径清单（``<loc>``）。

    解析失败一律返回空：畸形 sitemap 不该让整轮发现失败。
    """
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return ()
    out: list[str] = []
    for node in root.iter():
        if not str(node.tag).endswith("loc") or not node.text:
            continue
        path = _same_origin_path(node.text)
        if path and path not in out:
            out.append(path)
    return tuple(out)


def paths_for(name: str, text: str) -> tuple[str, ...]:
    """按文件名分派解析器；不认识的返回空。"""
    if name == ROBOTS:
        return paths_from_robots(text)
    if name == SITEMAP:
        return paths_from_sitemap(text)
    return ()


__all__ = [
    "ROBOTS",
    "SITEMAP",
    "WELL_KNOWN",
    "paths_for",
    "paths_from_robots",
    "paths_from_sitemap",
]
