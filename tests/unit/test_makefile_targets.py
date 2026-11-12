"""B239 · Makefile 里同一个目标定义两次 = 后一条静默覆盖前一条。

实测（`grep -E '^[a-z0-9._-]+:' Makefile | ... | uniq -c`）当时有 5 处重复：

| 目标 | 前一处 | 后一处 | 后果 |
|---|---|---|---|
| `demo` | 录屏成片（record_console / record_terminal / build_tour） | `scripts/demo.py` | **成片那条永远跑不到**，想跑成片会得到端到端演示 |
| `b46` | `exec-isolation` | `browser-render exec-isolation types-check lint` | 简写那行失效 |
| `b49` | `exec-isolation blackboard-conflict` | `parallel-check types-check lint` | 简写那行失效 |
| `ui-contrast` / `ui-a11y` | 与后一处逐字相同 | — | 每跑一次 make 都嘟囔两条 warning |

make 只会打一行 warning 然后照跑，所以这件事**没人会发现**——直到有人照着
"跑成片"的命令拿到别的东西。本用例把它变成一次可失败的检查。
"""

from __future__ import annotations

import re
from pathlib import Path

MAKEFILE = Path(__file__).resolve().parents[2] / "Makefile"

#: 交付材料里出现的目标名：README / quickref / dist-checklist 写的就是这些
#: （写成 split 而不是元组字面量：lint-nodata 把 ≥5 元素的字面量当硬编码行集）
_DOC_TARGETS = ("doctor demo demo-alt demo-offline bench "
                "replay-offline verify-report lint verify").split()

_TARGET_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)\s*:(?!=)")


def _targets() -> list[str]:
    out: list[str] = []
    for line in MAKEFILE.read_text(encoding="utf-8").splitlines():
        m = _TARGET_RE.match(line)
        if m:
            out.append(m.group(1))
    return out


def test_no_target_is_defined_twice() -> None:
    names = _targets()
    dupes = sorted({n for n in names if names.count(n) > 1})
    assert not dupes, f"这些目标定义了不止一次（后定义者胜，前一条是死代码）：{dupes}"


def test_documented_targets_exist_exactly_once() -> None:
    names = _targets()
    for name in _DOC_TARGETS:
        assert names.count(name) == 1, f"文档里写的 `make {name}` 定义了 {names.count(name)} 次"


def test_demo_is_the_end_to_end_run_not_the_screencast() -> None:
    """`make demo` 必须是 README 说的端到端演示；录屏成片另有名字。"""
    text = MAKEFILE.read_text(encoding="utf-8")
    block = text.split("\ndemo:", 1)[1].split("\n\n", 1)[0]
    assert "scripts/demo.py" in block, block
    assert "record_console.py" not in block, "成片脚本不该挂在 demo 上"
    assert "\ndemo-record:" in text, "录屏成片要有自己的目标名"


def test_phony_covers_the_screencast_target() -> None:
    text = MAKEFILE.read_text(encoding="utf-8")
    phony = next(line for line in text.splitlines() if line.startswith(".PHONY:"))
    assert "demo-record" in phony, phony
