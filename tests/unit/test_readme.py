"""B83 · README 与 CLI 的一致性（H25 判据 C–E）。

文档最常见的腐烂方式不是"写错"，是**写得当时对、之后没人改**。
所以这里机器校验：README 里出现的每条 `hos_ares <子命令>` 必须真能跑。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

README = Path(__file__).resolve().parents[2] / "README.md"
TEXT = README.read_text(encoding="utf-8")

#: 形如 `hos-ares chat` / `hos-ares run --target …` 的行内命令
_CMD = re.compile(r"hos_ares\s+([a-z][a-z0-9-]*)")


def _commands_in_readme() -> set[str]:
    """抽出 README 里提到的子命令名（跳过 `--help` 这类选项）。"""
    return {m.group(1) for m in _CMD.finditer(TEXT)}


# ── 结构 ──────────────────────────────────────────────────────
def test_readme_exists_and_has_quickstart() -> None:
    assert README.exists(), "顶层 README 不存在"
    first_screen = TEXT[:2200]
    for need in ("pip install", "hos-ares doctor", "hos-ares chat"):
        assert need in first_screen, f"首屏缺 {need!r}"


def test_readme_covers_three_pillars() -> None:
    for pillar in ("hos-ares run", "hos-ares platform", "hos-ares chat"):
        assert pillar in TEXT, f"缺 {pillar}"


# ── C/E：命令与 COMMANDS 一致 ─────────────────────────────────
def test_every_readme_command_exists() -> None:
    from hos_ares_cli.__main__ import COMMANDS

    known = {name for name, _, _ in COMMANDS}
    used = _commands_in_readme()
    unknown = used - known
    assert not unknown, f"README 里写了不存在的子命令：{sorted(unknown)}"


def test_all_pillars_are_registered() -> None:
    from hos_ares_cli.__main__ import COMMANDS

    known = {name for name, _, _ in COMMANDS}
    musts = ("chat", "run", "platform") + ("doctor", "console", "parity")
    for must in musts:
        assert must in known, f"{must} 没在 COMMANDS 里"


# ── D：诚实清单 ───────────────────────────────────────────────
def test_readme_states_limits_honestly() -> None:
    """必须有一节写"不能做什么"，且点名卡点，不许只写"敬请期待"。"""
    plain = TEXT.replace("**", "")          # 标题里有粗体，去掉再匹配
    assert "不能做什么" in plain, "缺能力边界一节"
    for blocker in ("docker", "模型", "召回"):
        assert blocker in TEXT, f"边界说明里没提 {blocker}"
    assert "敬请期待" not in TEXT, "不要用这种含糊说法代替卡点"


def test_no_unverified_claims() -> None:
    """README 里的每条能力**要么标未验，要么给出能当场复核的命令**（B536）。

    这条原来写死"容器真隔离后面必须跟未验"。E1 验过之后那句话变成假的了，
    但**判据不该跟着变成"随便写绿"**——所以换成不变量：
    凡声明 ✅ 的能力，同一行必须点名证据（测试文件 / 命令）。
    未验的能力照旧必须写清卡点。
    """
    lines = [ln for ln in TEXT.splitlines() if "容器真隔离" in ln and ln.lstrip().startswith(("|", "-", "*"))]
    assert lines, "缺容器隔离的说明"
    checked = 0
    for line in lines:
        if "✅" in line:
            checked += 1
            assert "test_sandbox_isolation" in line or "parity" in line, (
                f"声明容器隔离已验却没给复核入口：{line}"
            )
        elif "未验" in line:
            checked += 1
    assert checked, "容器隔离既没标已验也没标未验"
