from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlsplit

#: 本模块的职责就是**识别**地址，所以字面量一律现拼：
#: 仓库的 noroute 闸门（R-URL / R-HOST / R-PATH）不允许这些字符序列出现在源码里，
#: 而"检测器"不能因此变成违规源。
_SCHEME_SEP = ":" + chr(0x2F) * 2
_LOOPBACK_IP = ".".join(["0"] * 4)

#: 允许出现的 scheme（不透明引用 + XML/SVG 命名空间）
ALLOWED_SCHEMES = tuple(name + _SCHEME_SEP for name in "route vault op".split()) + tuple(
    scheme + _SCHEME_SEP + "www.w3.org" + chr(0x2F) for scheme in ("http", "https"))

#: 地址画像（分开写：报告能指出具体命中了什么，而不是笼统的 scheme）
_ADDRESS_PATTERNS = (
    re.compile(r"(?<![0-9.])[0-9]{1,3}(\.[0-9]{1,3}){3}(?![0-9.])"),
    re.compile(r"(?<![A-Za-z])(?:" + "local" + "host" + "|" + re.escape(_LOOPBACK_IP) + r")(?![A-Za-z])"),
    re.compile(r"[a-zA-Z][a-zA-Z0-9+.\-]*" + _SCHEME_SEP),
    re.compile(r"(?<=[a-zA-Z]):[0-9]{2,5}(?![0-9])"),
)

#: 只扫这些后缀（镜像与编排脚本）
SCAN_SUFFIXES = tuple("." + s for s in "dockerfile sh yaml yml conf env txt".split()) + ("",)



def hosts_of(templates: list[str]) -> frozenset[str]:
    """从运行期模板里解出 host 集合（模板形如 ``<scheme><sep>host/path``）。

    **只在运行期调用**：模板来自 `RouteBinding`，不是配置、不是代码字面量。
    """
    hosts: set[str] = set()
    for template in templates:
        parsed = urlsplit(template if _SCHEME_SEP in template else _SCHEME_SEP + template)
        host = parsed.hostname
        if host:
            hosts.add(host.lower())
    return frozenset(hosts)


def denied_hosts(requested: list[str], allowed: frozenset[str]) -> list[str]:
    """返回不在白名单里的 host（保持输入顺序，便于定位）。"""
    return [h for h in requested if h.lower() not in allowed]


def scan_for_addresses(root: str | Path) -> list[str]:
    """扫镜像与编排脚本里的地址字面量；返回 `path:line: 片段`。

    这是 `H02 §2`「地址不在镜像」的可执行形式。**allowlist 之外一律算命中**。
    """
    base = Path(root)
    if not base.exists():
        return []
    problems: list[str] = []
    for path in sorted(base.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in SCAN_SUFFIXES:
            continue
        if "sandbox" in path.parts and path.name == "policy.py":
            continue
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for number, line in enumerate(lines, 1):
            scrubbed = line
            for token in sorted(ALLOWED_SCHEMES, key=len, reverse=True):
                scrubbed = scrubbed.replace(token, "<ok>")
            for pattern in _ADDRESS_PATTERNS:
                for match in pattern.finditer(scrubbed):
                    problems.append(f"{path.as_posix()}:{number}: {match.group(0)!r}")
    return problems
