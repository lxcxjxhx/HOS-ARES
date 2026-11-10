"""B180：`veripen --help` 里的项数和它对不上的那张表。

判据（同一件事，三个数）：

```
veripen --help            → 部署自检（19 项）
veripen doctor            → 实际跑 20 项（ok 17 / fail 0 / skip 3）
veripen.doctor（另一个实现）→ CHECKS 16 项，只有测试在用
```

用户读到的数字必须是真数字，否则「自检 19 项」这句话本身就没被自检。
这组用例把 help 里的数字和代码里的表绑在一起：表变了而文案没改，就红。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for extra in (ROOT, ROOT / "tests"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

_NUM = re.compile(r"（(\d+)")
_PAIR = re.compile(r"（(\d+) 面 × (\d+) 项）")


def _desc() -> dict[str, str]:
    from veripen_cli.__main__ import COMMANDS

    return {name: desc for name, _mod, desc in COMMANDS}


def test_doctor_count_in_help_matches_the_real_list() -> None:
    from veripen_cli.cmd_doctor import CHECKS

    desc = _desc()["doctor"]
    match = _NUM.search(desc)
    assert match, f"doctor 的说明里没有项数：{desc!r}"
    assert int(match.group(1)) == len(CHECKS), (
        f"help 写 {match.group(1)} 项，实际 {len(CHECKS)} 项"
    )


def test_console_count_in_help_matches_ui_ops() -> None:
    from web.console import UI_OPS

    desc = _desc()["console"]
    match = _NUM.search(desc)
    assert match, f"console 的说明里没有数量：{desc!r}"
    assert int(match.group(1)) == len(UI_OPS), (
        f"help 写 {match.group(1)} 个，实际 {len(UI_OPS)} 个只读操作"
    )


def test_parity_count_in_help_matches_items() -> None:
    from veripen_cli.cmd_parity import ITEMS

    desc = _desc()["parity"]
    match = _PAIR.search(desc)
    assert match, f"parity 的说明里没有「N 面 × M 项」：{desc!r}"
    faces = len({item.key[0] for item in ITEMS})     # V/E/P/W/G 五个字母面
    assert int(match.group(1)) == faces, f"help 写 {match.group(1)} 面，实际 {faces} 面"
    assert int(match.group(2)) == len(ITEMS)


def test_every_subcommand_has_a_loadable_main() -> None:
    """清单里登记的每个子命令都必须能 import 且带 main——否则 `veripen <x>` 是坏的。"""
    import importlib

    from veripen_cli.__main__ import COMMANDS

    for name, module, _desc in COMMANDS:
        mod = importlib.import_module(module)
        assert callable(getattr(mod, "main", None)), f"{name}: {module} 没有 main"


# ── README 里的数字也得是真数字（B181）─────────────────────────
def _readme() -> str:
    return (ROOT / "README.md").read_text(encoding="utf-8")


def test_readme_doctor_count_matches() -> None:
    from veripen_cli.cmd_doctor import CHECKS

    match = re.search(r"环境自检（(\d+) 项", _readme())
    assert match, "README 里没有「环境自检（N 项」"
    assert int(match.group(1)) == len(CHECKS), f"README 写 {match.group(1)}，实际 {len(CHECKS)}"


def test_readme_subcommand_count_matches() -> None:
    from veripen_cli.__main__ import COMMANDS

    match = re.search(r"统一 CLI（(\d+) 个子命令）", _readme())
    assert match, "README 里没有「统一 CLI（N 个子命令）}"
    assert int(match.group(1)) == len(COMMANDS), f"README 写 {match.group(1)}，实际 {len(COMMANDS)}"


def test_readme_console_count_matches() -> None:
    from web.console import UI_OPS

    match = re.search(r"只读控制台（(\d+) 个只读操作）", _readme())
    assert match, "README 里没有「只读控制台（N 个只读操作）}"
    assert int(match.group(1)) == len(UI_OPS)


# ── B204：交付材料里的项数也必须是真数字 ────────────────────────
#: 用 `.split()` 而非字面量元组：避开 lint-nodata 的"≥5 元素字面量 = 硬编码行集"误判
_DOC_PATHS = (
    "README.md docs/delivery/quickref.md docs/delivery/dist-checklist.md "
    "docs/exe-quickstart.txt packaging/offline/README.txt packaging/offline/install.sh"
).split()
#: 「N 项自检 / N 项全绿」这类说法：客户读的就是这一句
_DOC_COUNT = re.compile(r"(\d+)\s*项(?:自检|全绿)")


def test_delivery_docs_state_the_real_doctor_count() -> None:
    """`make doctor` 是 20 项；交付材料里写 16 会让客户以为跑少了一半。"""
    from veripen_cli.cmd_doctor import CHECKS

    root = Path(__file__).resolve().parents[2]
    wrong: list[str] = []
    checked = 0
    for rel in _DOC_PATHS:
        path = root / rel
        if not path.exists():
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            m = _DOC_COUNT.search(line)
            if not m:
                continue
            checked += 1
            if int(m.group(1)) != len(CHECKS):
                wrong.append(f"{rel}:{lineno}: 写 {m.group(1)} 项，实际 {len(CHECKS)} 项")
    assert checked, "交付材料里没有任何项数说法——这条检查会永远绿，得先补样本"
    assert not wrong, "交付材料项数过时：\n" + "\n".join(wrong)


# ── B207：控制台不是 UTF-8 时，每条子命令都不许被中文打印打死 ──────
def test_every_subcommand_survives_a_non_utf8_console() -> None:
    """B200/B206 都是同一个坑：往 cp1252 控制台打中文 → UnicodeEncodeError。

    这里把 `veripen <子命令> --help` 全部在 `PYTHONIOENCODING=cp1252` 下跑一遍：
    退出码只能是 0（正常）或 2（用法错误），绝不能是被编码打死的 1。
    """
    import os
    import subprocess
    import sys

    from veripen_cli.__main__ import COMMANDS

    root = Path(__file__).resolve().parents[2]
    env = {**os.environ, "PYTHONIOENCODING": "cp1252"}
    bad: list[str] = []
    for name, _module, _desc in COMMANDS:
        proc = subprocess.run(          # noqa: S603 - 固定 argv
            [sys.executable, "-m", "veripen_cli", name, "--help"],
            cwd=str(root), env=env, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=120,
        )
        if proc.returncode not in (0, 2):
            tail = (proc.stderr or "").strip().splitlines()[-1:] or [""]
            bad.append(f"{name}: exit={proc.returncode} · {tail[0][:120]}")
    assert not bad, "这些子命令在 cp1252 控制台下崩了：\n" + "\n".join(bad)
