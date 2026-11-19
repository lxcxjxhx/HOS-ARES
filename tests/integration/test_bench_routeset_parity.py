"""B458 · 两份 `_routeset` 必须给出同一个顺序。

`scripts/b12/run_bench.py` 与 `hos_ares_cli/cmd_bench_leads.py` 各写了一份
「按标签造 RouteSet」的实现。B457 之前只有前者按模板字典序固定，后者直接返回
resolver 的 sha 序——同一标签两条面时，两边选中的是**不同的面**（B455 里 N-07
就是这么在进程内与真网络之间分叉的）。

抽成一份会牵动「产品 CLI 依赖 scripts/ 还是 tests/」这类结构问题；在那之前，
先钉一条**等值断言**：两份实现对同一标签必须给出相同的模板序列。
分叉那天它会红，红在的是一句"你们俩不一样"，而不是某个 oracle 的古怪结论。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
for extra in (ROOT, ROOT / "tests", ROOT / "tests" / "integration"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

#: 挑几个标签：前两个是**多面**的（`redirect_entry` 从 B384 起就有两条），
#: 单面标签用来确认不是「反正都只有一条」才相等。
TAGS = ("redirect_entry", "param_injectable", "object_read")


@pytest.fixture(scope="module")
def lab_base():
    from lab.app import App  # noqa: PLC0415
    from lab.net_server import LabServer  # noqa: PLC0415

    srv = LabServer(App(secure=False)).start()
    try:
        yield srv.base_url
    finally:
        srv.stop()


def test_two_routeset_implementations_agree(lab_base: str) -> None:
    sys.path.insert(0, str(ROOT / "scripts" / "b12"))
    import run_bench as RB  # noqa: PLC0415

    from hos_ares_cli import cmd_bench_leads as CBL  # noqa: PLC0415

    for tag in TAGS:
        a = [b.template for b in RB._routeset(lab_base, (tag,)).bindings]
        b = [b.template for b in CBL._routeset(lab_base, tag).bindings]
        assert b == a, f"{tag}：两份 _routeset 的顺序不同\n  run_bench: {a}\n  bench_leads: {b}"
        assert b == sorted(b), f"{tag}：顺序不是模板字典序（{b}）——多面标签下会随机换面"
