#!/usr/bin/env python3
"""lint-noroute —— 零地址闸门（27 §7 的可执行形式）。

规则：
  R-PATH   引号内以 / 开头的字面量            （全文件）
  R-URL    scheme://                           （全文件）
  R-HOST   localhost/127.0.0.1/0.0.0.0/*.local （全文件）
  R-PORT   port=<n> 或 :<n>/tcp|udp            （全文件）
  R-VERB   "GET /..." 形式                     （仅代码/配置）
  R-DEFAULT fallback_path 等兜底符号            （仅代码/配置）

扫描范围：产品目录 + 根级构建文件。刻意**不扫** plan-veripen/、docs/、vendor/、
.dsh/、scripts/ —— 前三者是规划包与生态源码（含必须引用的反例与 URL），
scripts/ 是构建/拉取工具（含官方仓库 URL 与 lint 自身规则），都不是“可被复制的路由代码”。

allowlist：config/noroute.allowlist，每行 ``<正则>\\t<理由>``，上限 6 行；缺理由即报错。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PRODUCT_DIRS = (
    "veripen_core",
    "veripen_oracles",
    "veripen_contract",
    "api",
    "worker",
    "web",
    "config",
    "profiles",
    "tests",
)
ROOT_FILES = ("pyproject.toml", "docker-compose.yml", "Makefile")
CODE_EXT = {
    ".py", ".ts", ".js", ".mjs", ".cjs", ".json", ".yaml", ".yml",
    ".sql", ".sh", ".toml", ".ini", ".cfg",
}
MAX_ALLOWLIST = 6

#: 录制夹具整目录豁免：夹具存的就是“录下来的真实模板”，不是可复制进代码的路由
EXEMPT_PREFIXES = (
    "tests/fixtures",
    "tests/routes/fixtures",
    # 靶场：B5 起的本地被测应用必须写出它自己的端点表（这是"被测对象"，不是"我们的路由"）。
    # 产品代码仍然零模板；此处豁免仅在 tests/lab 内生效。
    "tests/lab",
    # 契约夹具：存的是"客户发布的契约原文"，里面的路径/服务地址正是被测对象
    "tests/fixtures/contract",
)

RE_PATH = re.compile(r"""(?P<q>['"])(?P<v>/[^'"\n]*)(?P=q)""")
RE_URL = re.compile(r"""[a-zA-Z][a-zA-Z0-9+.\-]*://""")
RE_HOST = re.compile(r"""\b(localhost|127\.0\.0\.1|0\.0\.0\.0)\b|\.(local|internal|svc|cluster\.local)\b""")
RE_PORT = re.compile(r"""port=\d+|:\d+/(tcp|udp)\b""")
RE_VERB = re.compile(r"""['"](GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+/""")
#: 禁止出现的“找不到就用 X”类符号（N4 / 27 §3.9 末段）
BANNED_SYMBOLS: tuple[str, ...] = (
    "fallback_path",
    "default_route",
    "retry_with_literal",
    "DEFAULT_ENDPOINT",
    "DEFAULT_PATH",
)
RE_DEFAULT = re.compile(r"\b(" + "|".join(BANNED_SYMBOLS) + r")\b")

PATH_OK_PREFIX = ("/dev/null", "/tmp", "/<")
URL_OK = (
    "route://", "vault://", "op://", "file:///",
    "json-schema.org", "w3.org", "openapis.org", "sarif",
)


def _iter_files(root: Path = ROOT) -> list[Path]:
    files: list[Path] = []
    for d in PRODUCT_DIRS:
        base = root / d
        if base.exists():
            for p in base.rglob("*"):
                if not p.is_file() or "__pycache__" in p.parts:
                    continue
                rel = p.relative_to(root).as_posix()
                if any(rel.startswith(x) for x in EXEMPT_PREFIXES):
                    continue
                files.append(p)
    for name in ROOT_FILES:
        p = root / name
        if p.exists():
            files.append(p)
    return sorted(files)


def _load_allowlist(root: Path = ROOT) -> list[tuple[re.Pattern[str], str]]:
    fp = root / "config" / "noroute.allowlist"
    if not fp.exists():
        return []
    rows: list[tuple[re.Pattern[str], str]] = []
    for i, line in enumerate(fp.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if "\t" not in line:
            raise SystemExit(f"allowlist 第 {i} 行缺少 TAB 理由：{line!r}")
        pat, reason = line.split("\t", 1)
        if not reason.strip():
            raise SystemExit(f"allowlist 第 {i} 行理由为空：{line!r}")
        rows.append((re.compile(pat), reason.strip()))
    if len(rows) > MAX_ALLOWLIST:
        raise SystemExit(
            f"allowlist 有 {len(rows)} 行 > 上限 {MAX_ALLOWLIST}：先修代码，别加白"
        )
    return rows


def _scan(root: Path = ROOT) -> list[str]:
    allow = _load_allowlist(root)
    hits: list[str] = []
    for path in _iter_files(root):
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        rel = path.relative_to(root).as_posix()
        is_code = path.suffix in CODE_EXT or path.name == "Makefile"
        for lineno, line in enumerate(text.splitlines(), 1):
            if any(rx.search(line) for rx, _ in allow):
                continue
            rules: list[str] = []
            for m in RE_PATH.finditer(line):
                if not m.group("v").startswith(PATH_OK_PREFIX):
                    rules.append("R-PATH")
            for m in RE_URL.finditer(line):
                if not any(tok in m.group(0) or tok in line for tok in URL_OK):
                    rules.append("R-URL")
            if RE_HOST.search(line):
                rules.append("R-HOST")
            if RE_PORT.search(line):
                rules.append("R-PORT")
            if is_code:
                if RE_VERB.search(line):
                    rules.append("R-VERB")
                if RE_DEFAULT.search(line):
                    rules.append("R-DEFAULT")
            for rule in sorted(set(rules)):
                hits.append(f"{rel}:{lineno}: {rule}: {line.strip()[:120]}")
    return hits


def main(argv: list[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    root = ROOT
    if "--root" in args:
        root = Path(args[args.index("--root") + 1]).resolve()
    hits = _scan(root)
    if hits:
        print(f"noroute: FAIL: {len(hits)} 命中")
        for h in hits:
            print("  " + h)
        return 1
    print(f"noroute: OK: 0 命中（扫描 {len(_iter_files(root))} 文件）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
