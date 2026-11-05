"""B83 · README 与 CLI 的一致性（H25 判据 C–E）。

文档最常见的腐烂方式不是"写错"，是**写得当时对、之后没人改**。
所以这里机器校验：README 里出现的每条 `veripen <子命令>` 必须真能跑。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

README = Path(__file__).resolve().parents[2] / "README.md"
TEXT = README.read_text(encoding="utf-8")

#: 形如 `veripen chat` / `veripen run --target …` 的行内命令
_CMD = re.compile(r"veripen\s+([a-z][a-z0-9-]*)")


def _commands_in_readme() -> set[str]:
    """抽出 README 里提到的子命令名（跳过 `--help` 这类选项）。"""
    return {m.group(1) for m in _CMD.finditer(TEXT)}


# ── 结构 ──────────────────────────────────────────────────────
def test_readme_exists_and_has_quickstart() -> None:
    assert README.exists(), "顶层 README 不存在"
    first_screen = TEXT[:2200]
    for need in ("pip install", "veripen doctor", "veripen chat"):
        assert need in first_screen, f"首屏缺 {need!r}"


def test_readme_covers_three_pillars() -> None:
    for pillar in ("veripen run", "veripen platform", "veripen chat"):
        assert pillar in TEXT, f"缺 {pillar}"


# ── C/E：命令与 COMMANDS 一致 ─────────────────────────────────
def test_every_readme_command_exists() -> None:
    from veripen_cli.__main__ import COMMANDS

    known = {name for name, _, _ in COMMANDS}
    used = _commands_in_readme()
    unknown = used - known
    assert not unknown, f"README 里写了不存在的子命令：{sorted(unknown)}"


def test_all_pillars_are_registered() -> None:
    from veripen_cli.__main__ import COMMANDS

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
    """README 不许把未验项写成已完成。"""
    # 容器隔离那条必须带"未验"
    idx = TEXT.find("容器真隔离")
    assert idx > 0, "缺容器隔离的说明"
    window = TEXT[idx:idx + 120]
    assert "未验" in window, f"容器隔离被写成已完成：{window}"
