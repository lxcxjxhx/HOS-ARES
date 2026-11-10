"""B213：`veripen hunt` 的产物要带上逐条路由摘要，否则控制台路由页永远是空表。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def test_hunt_record_carries_binding_detail() -> None:
    """源码里必须有 `bindings_detail` 这一段，且带上限。

    这条用**源码断言**而不是跑一次 hunt：hunt 要真发包，属于集成档；
    这里守的是"别把这段删了"——删了以后控制台路由页会重新变空，而没人会发现。
    """
    src = (ROOT / "veripen_cli" / "cmd_hunt.py").read_text(encoding="utf-8")
    assert '"bindings_detail"' in src, "hunt 产物不再带逐条路由摘要"
    assert "BINDINGS_DETAIL_MAX" in src, "逐条摘要没有上限：产物会随路由数线性膨胀"
    # 模板正文不许落盘（22 §2）
    block = src.split('"bindings_detail"', 1)[1].split("],", 1)[0]
    assert "template_sha256" in block and '"template"' not in block, block[:200]


def test_detail_cap_is_sane() -> None:
    from veripen_cli.cmd_hunt import BINDINGS_DETAIL_MAX

    assert 10 <= BINDINGS_DETAIL_MAX <= 1000, BINDINGS_DETAIL_MAX
