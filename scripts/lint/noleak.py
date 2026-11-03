#!/usr/bin/env python3
"""lint-noleak —— 客户差异不许漏进内核（A2 / `01 §1` I3 的可执行形式）。

规则：**客户专有串只允许出现在该客户自己的 profile 与测试靶场里**，
不许出现在产品代码（`veripen_core/` `veripen_oracles/` `veripen_contract/`
`veripen/` `veripen_cli/` `api/` `worker/` `web/`）与根级构建文件里。

客户专有串从哪来：**从 `profiles/*/profile.yaml` 里读出来**，不手写。
手写一份黑名单是把自己的判断抄成常量——新加一个客户档它就漏了。
这里读的是 meta.customer_id / meta.name / meta.industry 与 auth 里的 username。

为什么这条与 `lint-noroute` 不同（两者不能互相替代）：
  - `lint-noroute` 管**地址**（URL/host/port/path）——防 hardroute 复活；
  - `lint-noleak` 管**客户身份**（customer_id / 行业词 / 账号名）——防"能卖第二家"变成空话。
  一条 profile 里没有地址也可能满是客户串（industry: auto、username: alice）。

allowlist：`config/noleak.allowlist`，每行 ``<正则>\\t<理由>``，上限 6 行。
超限直接失败——上限存在的意义是"超了说明有真泄漏，先修代码"（`13` O9 的同一原则）。

用法：python scripts/lint/noleak.py [--json]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]

#: 产品代码目录。客户串出现在这里就是泄漏。
PRODUCT_DIRS = (
    "veripen_core",
    "veripen_oracles",
    "veripen_contract",
    "veripen",
    "veripen_cli",
    "api",
    "worker",
    "web",
)

#: 根级构建文件同样算产品面。
ROOT_FILES = ("pyproject.toml", "docker-compose.yml", "Dockerfile")

#: 扫描的扩展名。只扫代码与配置——正文文档里出现客户名是正常的（交付材料要写客户）。
CODE_EXT = {".py", ".ts", ".js", ".mjs", ".cjs", ".json", ".yaml", ".yml", ".sql", ".sh", ".toml"}

#: 允许出现在产品代码里的**通用词**。它们是从 profile 抽出来的，
#: 但本身是行业分类而非客户身份，写进内核不构成泄漏。
GENERIC_TOKENS = frozenset(
    {
        "saas", "auto", "lab", "internal", "confidential", "public",
        "basic", "jwt", "mtls", "oauth2", "session", "saml",
        "primary", "secondary", "admin", "user",
        "web", "api", "url", "uuid", "int", "json",
    }
)

MAX_ALLOWLIST = 6

ALLOWLIST = ROOT / "config" / "noleak.allowlist"


def _iter_profile_tokens() -> set[str]:
    """从每个 profile 抽出**该客户专有**的字符串。

    刻意不抽完整的 profile（那会把 scope 段的结构词也扫进来）：
    只取三处身份标识——customer_id / meta.name / auth 的 username。
    industry 单独处理：它在 GENERIC_TOKENS 里就直接跳过。
    """
    tokens: set[str] = set()
    for path in sorted((ROOT / "profiles").glob("*/profile.yaml")):
        try:
            doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:  # pragma: no cover - profile 坏掉另有测试
            raise SystemExit(f"profile 解析失败 {path}: {exc}") from exc
        meta = doc.get("meta") or {}
        for key in ("customer_id", "name"):
            value = meta.get(key)
            if isinstance(value, str) and value.strip():
                tokens.add(value.strip())
        industry = meta.get("industry")
        if isinstance(industry, str) and industry.strip().lower() not in GENERIC_TOKENS:
            tokens.add(industry.strip())
        for provider in (doc.get("auth") or {}).get("providers") or []:
            username = (provider or {}).get("username")
            if isinstance(username, str) and username.strip():
                tokens.add(username.strip())
    # 太短的串不扫：两三个字母几乎必然误命中（`web`、`api` 已进 GENERIC_TOKENS，
    # 但客户 id 可能是 `AB`）。门槛定 4 与 `noroute` 的路径门槛同一口径。
    return {t for t in tokens if len(t) >= 4 and t.lower() not in GENERIC_TOKENS}


def _iter_files() -> list[Path]:
    files: list[Path] = []
    for name in PRODUCT_DIRS:
        base = ROOT / name
        if not base.is_dir():
            continue
        for p in base.rglob("*"):
            if p.is_file() and p.suffix in CODE_EXT and "__pycache__" not in p.parts:
                files.append(p)
    for name in ROOT_FILES:
        p = ROOT / name
        if p.is_file():
            files.append(p)
    return sorted(files)


def _load_allowlist() -> tuple[list[tuple[str, str]], list[str]]:
    """返回 (规则, 问题)。格式：``<正则>\\t<理由>``，缺理由即错。"""
    rules: list[tuple[str, str]] = []
    problems: list[str] = []
    if not ALLOWLIST.is_file():
        return rules, problems
    for i, raw in enumerate(ALLOWLIST.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "\t" not in line:
            problems.append(f"noleak.allowlist 第 {i} 行缺理由（应为 <正则>\\t<理由>）")
            continue
        pattern, reason = line.split("\t", 1)
        if not reason.strip():
            problems.append(f"noleak.allowlist 第 {i} 行理由为空")
            continue
        rules.append((pattern.strip(), reason.strip()))
    if len(rules) > MAX_ALLOWLIST:
        problems.append(f"noleak.allowlist 有 {len(rules)} 行，超过上限 {MAX_ALLOWLIST}")
    return rules, problems


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    tokens = _iter_profile_tokens()
    rules, problems = _load_allowlist()
    hits: list[dict[str, object]] = []

    import re

    for path in _iter_files():
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        rel = path.relative_to(ROOT).as_posix()
        for lineno, line in enumerate(text.splitlines(), 1):
            if any(re.search(pat, line) for pat, _ in rules):
                continue
            low = line.lower()
            for token in tokens:
                if token.lower() in low:
                    hits.append({"file": rel, "line": lineno, "token": token,
                                 "text": line.strip()[:120]})
                    break

    if args.json:
        print(json.dumps({"files": len(_iter_files()), "tokens": sorted(tokens),
                          "hits": hits, "problems": problems}, ensure_ascii=False, indent=2))
    else:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        for h in hits:
            problems.append(f"{h['file']}:{h['line']} 出现客户串 {h['token']!r}: {h['text']}")
        for p in problems:
            print(f"NOLEAK {p}")
        print(f"lint-noleak: {'OK' if not problems else 'FAIL'}: "
              f"{len(hits)} 命中（扫描 {len(_iter_files())} 文件，"
              f"{len(tokens)} 个客户串）")
    return 0 if not problems else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
