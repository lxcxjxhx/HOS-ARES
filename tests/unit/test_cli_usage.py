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
