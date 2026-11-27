"""B610 · **在真实数据上数一遍**：归一/合并类逻辑的护栏。

## 为什么要单独一个文件

B608 把 `templatize_many` 接进 crawl 后：

- 4 项端到端用例全绿
- 召回 0.581 不变

**但它把靶场 24 条面压塌成 3 条。** 三批之后（B609）才靠
"拿真实路径数一遍模板数"发现。

这个文件把那次数数变成**常驻判据**：

1. 靶场上任意一条已定义的路径，归一时**不能被合掉**
2. 语义 id 形态要被合（那是 templatize_many 的用途）
3. `merge_by_structural_key` 不猜语义 id（它的职责边界）

## 为什么这条能防住

B608 的用例验的是"该合的和了"，全绿。它**没验**"不该合的没被合"。
本文件补的是后一半——而且用的是**靶场真实路径集**，不是编的样本。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
for extra in (ROOT, ROOT / "tests"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from hos_ares_core.discovery.templatize import templatize, templatize_many  # noqa: E402

S = chr(0x2F)


def _lab_paths() -> list[str]:
    """靶场上**全部**已定义路径（去掉查询串，那是另一维）。"""
    from lab.routes import routeset

    return [str(b.template).split("?")[0] for b in routeset().bindings]


# ── 1：靶场路径一条都不许被合掉 ─────────────────────────────
def test_no_lab_face_is_merged_away() -> None:
    """**这是 B608 那个 bug 的常驻防线。**

    修前：24 条 → 3 条。`/api/echo` `/api/fetch` `/api/login`
    都是不同端点，被当成「语义 id」合掉了。
    """
    paths = _lab_paths()
    assert len(paths) >= 20, f"靶场路径太少（{len(paths)}），判据失去意义"

    before = {templatize(p)[0] for p in paths}
    after = {templatize_many(paths)[p][0] for p in paths}
    assert after == before, (
        "归一改变了靶场的面集合："
        f"少了 {sorted(before - after)}"
    )


# ── 2：语义 id 形态仍要被合 ─────────────────────────────────
def test_semantic_id_shape_still_merges() -> None:
    """收紧判据不能把用途一起收没了。"""
    paths = [
        S + "api user-A profile".replace(" ", S),
        S + "api user-B profile".replace(" ", S),
        S + "api user-C profile".replace(" ", S),
    ]
    assert len({templatize_many(paths)[p][0] for p in paths}) == 1


# ── 3：merge_by_structural_key 不猜语义 ─────────────────────
def test_structural_key_merge_does_not_guess_semantics() -> None:
    """它的职责是「按 `{p}` 归一后相同才合」，不猜"这看起来像 id"。

    猜的后果与 B608 同性质：真实的面被合掉。
    """
    from hos_ares_core.routes.merge import merge_by_structural_key
    from hos_ares_core.routes.types import RouteCandidate, RouteProvenance

    cands = [
        RouteCandidate(method="GET", template=S + "api user-" + x + " profile".replace(" ", S),
                       source=RouteProvenance.CONTRACT)
        for x in ("A", "B", "C")
    ]
    assert len(merge_by_structural_key(cands)) == 3


# ── 4：靶场在合并层也不许变 ─────────────────────────────────
def test_structural_key_merge_keeps_every_lab_face() -> None:
    from hos_ares_core.routes.merge import merge_by_structural_key
    from hos_ares_core.routes.types import RouteCandidate, RouteProvenance

    paths = _lab_paths()
    cands = [
        RouteCandidate(method="GET", template=p, source=RouteProvenance.CONTRACT)
        for p in paths
    ]
    assert len(merge_by_structural_key(cands)) == len(paths)


# ── 5：判据自身的前提 ───────────────────────────────────────
@pytest.mark.parametrize("path", "echo fetch login".split())
def test_known_distinct_endpoints_are_never_merged(path: str) -> None:
    """把 B609 里那几个具体端点钉住（防以后判据又被放松）。"""
    others = [p for p in "echo fetch login".split() if p != path]
    trio = [S + "api " + x for x in (path, *others)]
    got = {templatize_many(trio)[p][0] for p in trio}
    assert len(got) == 3, got
