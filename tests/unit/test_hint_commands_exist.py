"""B305 · 提示里出现的 `hos_ares <子命令>` 必须真的存在。

这一族已经犯过两次：bench 提示 `hos_ares evidence --verify-chain`（B293，那条命令不存在），
doctor 提示 `--only model`（B304，检查名是 `model_backend`）。共同点是**提示没人照抄过**。

扫描口径：读 `hos_ares_cli/` `hos_ares/` `hos_ares_core/` `web/` 里的源码，找 `hos_ares <词>`，
排除三种"不是提示"的写法（否定句与历史注记），其余必须落在 `COMMANDS` 里。
"""

from __future__ import annotations

import re
from pathlib import Path

from hos_ares_cli.__main__ import COMMANDS

ROOT = Path(__file__).resolve().parents[2]
SCAN_DIRS = ("hos_ares_cli", "hos_ares", "hos_ares_core", "web")
PATTERN = re.compile(r"hos_ares ([a-z][a-z_-]*)")
#: 这些词出现在 `hos_ares ` 后面时不是子命令（模块名 / 选项 / 帮助）
ALLOWED = {"help"}

#: 命中前面若出现这些词，说明是"没有这个子命令"之类的否定或历史注记
NEGATIONS = "没有 不叫 不是 不存在 旧 以前".split()


def _real_commands() -> set[str]:
    return {c[0] for c in COMMANDS} | ALLOWED


def _scan() -> list[tuple[str, int, str]]:
    hits: list[tuple[str, int, str]] = []
    for rel in SCAN_DIRS:
        for path in sorted((ROOT / rel).rglob("*.py")):
            text = path.read_text(encoding="utf-8")
            for match in PATTERN.finditer(text):
                line_start = text.rfind("\n", 0, match.start()) + 1
                line_end = text.find("\n", match.end())
                line = text[line_start:line_end if line_end != -1 else len(text)]
                # 否定词可能在前（"没有 `hos_ares cases`"）也可能在后
                # （"`hos_ares evidence` … 那个子命令不存在"），两边都看。
                if any(word in line for word in NEGATIONS):
                    continue
                line_no = text.count("\n", 0, match.start()) + 1
                hits.append((str(path.relative_to(ROOT)), line_no, match.group(1)))
    return hits


def test_every_mentioned_subcommand_exists() -> None:
    valid = _real_commands()
    bad = [(f, n, word) for f, n, word in _scan() if word not in valid]
    assert not bad, "提示里出现了不存在的子命令：" + ", ".join(
        f"{f}:{n} `hos_ares {w}`" for f, n, w in bad)


def test_docs_do_not_name_nonexistent_subcommands() -> None:
    """B352：**用户面**文档里也会写不存在的命令。

    只扫 README / exe-quickstart / delivery：`plan-hos-ares/` 是目标设计稿，
    里面写 `hos_ares cases`、`hos_ares db` 这类"计划中的命令"是正常的，不该被当缺陷。
    """
    valid = _real_commands()
    bad = []
    docs = [ROOT / "README.md", ROOT / "exe-quickstart.txt"]
    docs += sorted((ROOT / "delivery").rglob("*.md"))
    for path in [d for d in docs if d.is_file()]:
        text = path.read_text(encoding="utf-8")
        for match in PATTERN.finditer(text):
            line_start = text.rfind(chr(10), 0, match.start()) + 1
            line_end = text.find(chr(10), match.end())
            line = text[line_start:line_end if line_end != -1 else len(text)]
            if any(word in line for word in NEGATIONS):
                continue
            if match.group(1) not in valid:
                bad.append(f"{path.relative_to(ROOT)}: `hos_ares {match.group(1)}`")
    assert not bad, "文档里出现了不存在的子命令：" + ", ".join(bad)


def test_the_scan_actually_finds_something() -> None:
    """扫描器自己也要有信号：一条都扫不到说明正则或目录写错了。"""
    assert _scan(), "扫描没命中任何 hos_ares 子命令——口径失效"


def test_negation_filter_is_not_vacuous() -> None:
    """否定句要真的被放过，否则这条用例会永远绿。"""
    from tests.unit.test_hint_commands_exist import NEGATIONS

    for sample in ("没有 `hos_ares cases` 这个子命令",
                   "`hos_ares evidence` 那个子命令不存在"):
        assert any(word in sample for word in NEGATIONS), sample
