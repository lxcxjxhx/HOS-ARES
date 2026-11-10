"""B223：交付材料里的命令要有**跨平台**入口（Windows 上没有 make）。"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _tasks() -> dict[str, tuple[str, ...]]:
    spec = importlib.util.spec_from_file_location("_tasks", ROOT / "scripts" / "tasks.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return dict(mod.TASKS)


def _documented_make_targets() -> set[str]:
    used: set[str] = set()
    for rel in ("docs/delivery/quickref.md", "docs/delivery/dist-checklist.md"):
        text = (ROOT / rel).read_text(encoding="utf-8")
        used |= set(re.findall(r"\bmake ([A-Za-z][\w.\-]*)", text))
    return used


def test_documented_make_targets_exist_in_the_makefile() -> None:
    """文档里写的 `make X`，Makefile 里必须有 X——`make up` 曾经就不存在。"""
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    targets = {m.group(1) for m in re.finditer(r"^([a-zA-Z][\w.\-]*):", makefile, re.M)}
    missing = sorted(t for t in _documented_make_targets() if t not in targets)
    assert not missing, f"文档里的 make 目标不存在：{missing}"


def test_every_documented_target_has_a_task_runner_twin() -> None:
    """每个文档里的目标都要有 `scripts/tasks.py <同名>` 的等价物（跨平台）。"""
    tasks = _tasks()
    missing = sorted(t for t in _documented_make_targets() if t not in tasks)
    assert not missing, f"这些目标没有跨平台入口：{missing}"


def test_tasks_cover_the_essentials() -> None:
    tasks = _tasks()
    for name in ("doctor", "lint", "verify", "demo", "bench"):
        assert name in tasks, name


def test_unknown_task_exits_2() -> None:
    spec = importlib.util.spec_from_file_location("_tasks2", ROOT / "scripts" / "tasks.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.main(["no-such-task"]) == 2
