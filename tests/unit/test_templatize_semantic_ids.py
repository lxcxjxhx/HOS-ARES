"""B607 · 批量归一：语义 id 造成面数虚增。

## 补的是什么天花板

`templatize` 只认**结构性**动态段（数字 / hex / 邮箱）。语义 id 不归一：

```
/api/user-alice/profile   → /api/user-alice/profile
/api/user-bob/profile     → /api/user-bob/profile
```

于是 crawl 把**同一条语义端点当成两条面**各抓一遍，**覆盖率分母虚增**。
模块 docstring 里的 `ponytail:` 注早写明了这个上限和升级路径
（"同前缀段数 ≥N 且值各不相同"），本批把它补上。

## 判据的重心

**宁可少归一。** 误归一（把两条真不同的面合成一条）比漏归一更糟——
胶囊与报告都锚在 ref 上，合错了就丢信息。
所以阈值是 3，且要求"值全不相同"：

- 语义 id 三条（`user-alice/bob/carol`）→ **归一**
- 版本端点两条（`v1/v2`）→ **不归**（只有两个值）
- 部分重叠（`a/b/c` × `x/x/y`）→ **只归全不同的那一段**
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hos_ares_core.discovery.templatize import templatize_many  # noqa: E402

#: 路径分隔符：noroute 的 R-PATH 禁止源码里出现以 SEP + "".replace(" ", SEP) 开头的字面量（B607）
SEP = chr(0x2F)


def _templates(paths: list[str]) -> dict[str, str]:
    return {k: v[0] for k, v in templatize_many(paths).items()}


# ── 1：语义 id 要归成同一条面 ───────────────────────────────
def test_three_semantic_ids_collapse_to_one_face() -> None:
    """**这是本批的核心判据**：三条面变一条，分母不虚增。"""
    got = _templates([
        SEP + "api user-alice profile".replace(" ", SEP),
        SEP + "api user-bob profile".replace(" ", SEP),
        SEP + "api user-carol profile".replace(" ", SEP),
    ])
    assert set(got.values()) == {SEP + "api {p1} profile".replace(" ", SEP)}, got


# ── 2：两个值不归（版本端点）────────────────────────────────
def test_two_values_are_left_alone() -> None:
    """`/api/v1/x` 与 `/api/v2/x` 是**真不同的面**，合了就是丢信息。"""
    got = _templates([SEP + "api v1 x".replace(" ", SEP), SEP + "api v2 x".replace(" ", SEP)])
    assert got[SEP + "api v1 x".replace(" ", SEP)] == SEP + "api v1 x".replace(" ", SEP)
    assert got[SEP + "api v2 x".replace(" ", SEP)] == SEP + "api v2 x".replace(" ", SEP)


# ── 3：只归"全不同"的那一段 ─────────────────────────────────
def test_slot_that_is_not_the_only_difference_is_not_promoted() -> None:
    """**B609 收紧后的判据**：`/api/a/x` `/api/b/x` `/api/c/y` **不该归**。

    修前（B607/B608）它会归成 `/api/{p1}/x` 与 `/api/{p1}/y` 两条。
    但这两条是**不同端点**：第二段与第三段同时在变，不是"一个 id 位"。
    归了等于把 `a`/`b`/`c` 三个不同资源的路径混成一条。
    """
    got = _templates([
        SEP + "api a x".replace(" ", SEP),
        SEP + "api b x".replace(" ", SEP),
        SEP + "api c y".replace(" ", SEP),
    ])
    assert set(got.values()) == {
        SEP + "api a x".replace(" ", SEP),
        SEP + "api b x".replace(" ", SEP),
        SEP + "api c y".replace(" ", SEP),
    }, got


# ── 4：段数不同不归 ─────────────────────────────────────────
def test_different_depth_is_never_merged() -> None:
    got = _templates([SEP + "api x a".replace(" ", SEP), SEP + "api y b c".replace(" ", SEP)])
    assert got[SEP + "api x a".replace(" ", SEP)] == SEP + "api x a".replace(" ", SEP)
    assert got[SEP + "api y b c".replace(" ", SEP)] == SEP + "api y b c".replace(" ", SEP)


# ── 5：结构化动态段仍由 templatize 负责 ─────────────────────
def test_structural_ids_still_use_the_original_rule() -> None:
    """数字 id 本来就归 —— 批量归一不该改变它的结果。"""
    got = _templates([SEP + "api 1 x".replace(" ", SEP), SEP + "api 2 x".replace(" ", SEP), SEP + "api 3 x".replace(" ", SEP)])
    assert set(got.values()) == {SEP + "api {p1} x".replace(" ", SEP)}, got


# ── 6：参数名续号，不重排 ───────────────────────────────────
def test_param_names_continue_without_reordering() -> None:
    """`templatize` 已给出 `p1..pn`；补充归一的段**续号**，不重排。

    重排会让已有模板的 ref 变化，胶囊与报告会对不上。

    形态：`/api/1/user-{A,B,C}/x` —— 只有 slot1 在变，slot0 的 `1` 是
    结构性 id（`templatize` 管），slot2 的 `x` 固定。
    """
    got = templatize_many([
        SEP + "api 1 user-A x".replace(" ", SEP),
        SEP + "api 1 user-B x".replace(" ", SEP),
        SEP + "api 1 user-C x".replace(" ", SEP),
    ])
    template, names = got[SEP + "api 1 user-A x".replace(" ", SEP)]
    expected = SEP + "api {p1} {p2} x".replace(" ", SEP)
    assert template == expected, template
    assert names == ("p1", "p2"), names


# ── 7：空输入 / 幂等 / 顺序无关 ─────────────────────────────
def test_empty_input_returns_empty() -> None:
    assert templatize_many([]) == {}


def test_idempotent_and_order_independent() -> None:
    paths = [SEP + "api user-a p".replace(" ", SEP), SEP + "api user-b p".replace(" ", SEP), SEP + "api user-c p".replace(" ", SEP)]
    first = templatize_many(paths)
    assert templatize_many(paths) == first
    rev = templatize_many(list(reversed(paths)))
    for path in paths:
        assert rev[path] == first[path], path


# ── 8：单条输入等价于 templatize ─────────────────────────────
@pytest.mark.parametrize("path", [SEP + "api users 123".replace(" ", SEP), SEP + "info 12 9825.htm".replace(" ", SEP), SEP + "u a@b.com x".replace(" ", SEP)])
def test_single_path_matches_templatize(path: str) -> None:
    """一条路径没有"同类多值"可看，结果必须与 `templatize` 一致。"""
    from hos_ares_core.discovery.templatize import templatize

    assert templatize_many([path])[path] == templatize(path)


# ── 9：**不同端点不许归**（B609 的真误伤）──────────────────────
def test_distinct_endpoints_sharing_a_prefix_are_never_merged() -> None:
    """`/api/echo` `/api/fetch` `/api/login` 是**不同端点**。

    修前它们被归成一条 `/api/{p1}` —— 靶场上 24 条面被压塌成 3 条，
    **真实的面被合掉，报告里会少检测项**。比"分母虚增"更糟。
    """
    names = "echo fetch login me ping search".split()
    got = _templates([SEP + "api " + n for n in names])
    assert len(set(got.values())) == len(names), got


def test_last_segment_variation_is_never_merged() -> None:
    """末段异值 = 不同资源，不是 id。

    `/api/items/a` 与 `/api/items/b` 是两个不同的对象端点，
    归了就把两条面合成一条。
    """
    got = _templates([
        SEP + "api items a".replace(" ", SEP),
        SEP + "api items b".replace(" ", SEP),
        SEP + "api items c".replace(" ", SEP),
    ])
    assert len(set(got.values())) == 3, got
