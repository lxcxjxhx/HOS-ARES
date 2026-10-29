"""`make lint-noleak` —— 内核零客户串检查（A2 断言的机械实现，`01 §1` 铁律 #2）。

检查 `veripen_core/` 与 `veripen_oracles/` 中是否出现客户标识。
客户标识的唯一来源是 `profiles/*/profile.yaml` 的 `meta.customer_id` 与域名/公司名——
所以本脚本**动态读取 profile**，而不是硬编码一份黑名单（否则第二家客户就要改代码，
恰好违反 A1/A2 的用意）。

额外查：`core`/`oracles` 里出现具体 URL 主机、IP 段、车型名等环境特征也算泄漏。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCAN_PKGS = ["veripen_core", "veripen_oracles"]
PROFILES_DIR = ROOT / "profiles"
SKIP_PARTS = {"__pycache__", ".venv"}


def load_customer_tokens() -> set[str]:
    """从所有 profile 抽出客户标识 token（customer_id / meta.name / owner 域名主体）。"""
    tokens: set[str] = set()
    if not PROFILES_DIR.exists():
        return tokens
    for profile in sorted(PROFILES_DIR.glob("*/profile.yaml")):
        text = profile.read_text(encoding="utf-8")
        for m in re.finditer(r"^\s*customer_id:\s*(.+)$", text, re.MULTILINE):
            tokens.add(m.group(1).strip().strip("'\""))
        for m in re.finditer(r"^\s*name:\s*(.+)$", text, re.MULTILINE):
            val = m.group(1).strip().strip("'\"")
            # profile 名本身（如 demo-a）也不该出现在 core 里
            if val:
                tokens.add(val)
        for m in re.finditer(r"^\s*value:\s*['\"]?([a-z0-9.-]+\.[a-z]{2,})", text, re.MULTILINE):
            tokens.add(m.group(1).strip().strip("'\""))
    # 过短的 token 误报率高（如 "a"），只保留 >=3 字符
    return {t for t in tokens if len(t) >= 3}


#: 与客户无关、但属于"环境特征"的硬编码形态。
#:
#: 注意 CIDR 的判据：**私网/保留段是安全要求，不是泄漏**。
#: `05 §1.1` 明确要求 deny 列表默认含 169.254.169.254/32、10/8、172.16/12、192.168/16、::1、fd00::/8——
#: 把这些当泄漏会让 lint 与规格直接冲突。所以只有**可路由的**CIDR 才算硬编码目标泄漏。
ENV_PATTERNS = [
    (re.compile(r"https?://(?!localhost|127\.0\.0\.1|example\.invalid|example\.com|\{)[a-z0-9.-]+\.(?:com|cn|net|io|local)"),
     "hardcoded external host"),
]

CIDR_RE = re.compile(r"\b((?:\d{1,3}\.){3}\d{1,3}/\d{1,2})\b")


def _is_reserved_cidr(text: str) -> bool:
    """私网 / 环回 / 链路本地 / 保留段 → 属于 deny 安全清单，不算泄漏。"""
    import ipaddress

    try:
        net = ipaddress.ip_network(text, strict=False)
    except ValueError:
        return False
    return net.is_private or net.is_loopback or net.is_link_local or net.is_reserved or net.is_unspecified


def main() -> int:
    tokens = load_customer_tokens()
    hits: list[str] = []

    for pkg in SCAN_PKGS:
        base = ROOT / pkg
        if not base.exists():
            continue
        for path in sorted(base.rglob("*.py")):
            if any(part in SKIP_PARTS for part in path.parts):
                continue
            rel = path.relative_to(ROOT)
            for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                stripped = line.strip()
                # 注释与字符串里的示例不算泄漏源，但为了"机械可判"仍检查：宁可误报也不漏报
                for tok in tokens:
                    if tok in stripped:
                        hits.append(f"{rel}:{lineno}: customer token {tok!r} in core/oracles")
                for pattern, label in ENV_PATTERNS:
                    if pattern.search(stripped):
                        # 允许文档字符串里描述性提到 example.invalid（已在正则里排除）
                        hits.append(f"{rel}:{lineno}: {label} -> {stripped[:90]}")
                # 可路由 CIDR 才是"硬编码客户目标"；保留段属 deny 安全清单
                for cidr in CIDR_RE.findall(stripped):
                    if not _is_reserved_cidr(cidr):
                        hits.append(f"{rel}:{lineno}: hardcoded routable CIDR {cidr} -> {stripped[:90]}")

    if hits:
        print(f"lint-noleak: {len(hits)} violation(s)  (customer tokens: {sorted(tokens)})")
        for h in hits:
            print(f"  - {h}")
        return 1
    print(f"lint-noleak: 0 violations  (scanned customer tokens: {sorted(tokens) or 'none'})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
