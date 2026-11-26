"""B538 · 契约标签采纳：结构推断能多跑出多少可测面（V4 的第三因）。

`H05 §3` 把「真实目标召回 0.33」拆成三条原因，第三因是**标签没被采纳**：
契约里那些没有显式 `x-hos-ares-tags` 的 operation，结构推断早算出了 `proposed`，
但 `plan()` 的调用方不传 `confirm`，于是全被记 `no_tags` 跳过。

这一批补的不是新推断器，是**出口**：
- 两条命令（`run` / `hunt`）用同一套开关，不许"同一份契约在两条命令下能跑的面不一样"；
- 采纳后 `source` 仍是 `structural`、置信 0.80——**猜错要能一眼看出这条面是靠猜的**；
- 默认行为不变（不传就不采纳），否则等于把"没确认的推断"当成"契约声明"。
"""

from __future__ import annotations

from typing import Any

import pytest

from hos_ares_contract.parser import parse_openapi
from hos_ares_contract.planner import plan
from hos_ares_core.routes import RouteProvenance

#: 一份**没有任何显式标签**的契约：三面全是结构可认的
UNTAGGED_SPEC = """
openapi: 3.0.0
info: {title: t, version: "1"}
paths:
  /users/{id}:
    get:
      parameters:
        - {name: id, in: path, required: true, schema: {type: string}}
      responses:
        "200":
          description: ok
          content:
            application/json:
              schema:
                type: object
                properties: {id: {type: string}, name: {type: string}}
  /items:
    get:
      parameters:
        - {name: page, in: query, schema: {type: integer}}
      responses:
        "200":
          description: ok
          content:
            application/json:
              schema: {type: object}
  /search:
    get:
      parameters:
        - {name: q, in: query, schema: {type: string}}
      responses:
        "200":
          description: ok
          content:
            application/json:
              schema: {type: object}
"""


def _opset() -> Any:
    return parse_openapi(UNTAGGED_SPEC, asset_id="fixture.invalid", allowed_hosts=["fixture.invalid"])


# ── 默认行为：只采纳"形状即声明"的那几个 ──────────────────────
def test_without_the_switch_only_self_evident_tags_are_adopted() -> None:
    """默认就有的只有 `SELF_EVIDENT`（形状即声明，如分页 → object_list）。

    其余 (`object_read` / `param_injectable`) 必须 operator 确认才采纳——
    "看起来像单对象读"不等于"它就是单对象读"。
    """
    result = plan(_opset())
    adopted = {
        tag
        for cand in result.candidates
        for tag, src in cand.response_shape["tag_sources"].items()
        if src == "structural_adopted"
    }
    assert adopted == {"object_list"}, adopted
    assert {"object_read", "param_injectable"} & set(adopted) == set()
    reasons = {why for _op, why in result.skipped}
    assert "awaiting_operator" in reasons, reasons


# ── 开开关：结构可认的面真能出候选 ────────────────────────────
def test_structural_adoption_yields_candidates() -> None:
    without = plan(_opset())
    with_struct = plan(_opset(), accept_structural=True)
    assert len(with_struct.candidates) > len(without.candidates)
    assert len(with_struct.candidates) >= 3, (
        f"结构可认的三面没都出来：{len(with_struct.candidates)}"
    )


def test_adopted_tags_keep_their_provenance() -> None:
    """采纳 ≠ 洗白：来源是 `structural_adopted`（形状即声明）或 `operator`（被确认），
    **绝不会**变成 `contract`；置信 0.80 / 0.90 低于契约显式的 0.95。"""
    result = plan(_opset(), accept_structural=True)
    sources: dict[str, str] = {}
    confidence: dict[str, float] = {}
    for cand in result.candidates:
        sources.update(cand.response_shape["tag_sources"])
        confidence.update(cand.response_shape["tag_confidence"])
    assert sources, "候选上没带来源"
    assert "contract" not in sources.values(), f"结构推断被洗成契约声明：{sources}"
    assert set(sources.values()) <= {"structural_adopted", "structural", "operator"}, sources
    assert all(v <= 0.90 for v in confidence.values()), confidence


# ── 只采纳点名的那些（operator 确认的出口）────────────────────
def test_explicit_confirm_limits_adoption() -> None:
    everything = plan(_opset(), accept_structural=True)
    one_tag = plan(_opset(), confirm={"object_read"})
    assert 0 < len(one_tag.candidates) < len(everything.candidates)


# ── 两条命令同一个出口 ────────────────────────────────────────
@pytest.mark.parametrize("module", ["hos_ares_cli.cmd_run", "hos_ares_cli.cmd_hunt"])
def test_both_commands_expose_the_same_switches(module: str) -> None:
    """`hunt` 缺这两个开关时，同一份契约在两条命令下能跑的面不一样（B538 修的就是这个）。"""
    import importlib
    import inspect

    src = inspect.getsource(importlib.import_module(module))
    for flag in ("--confirm-structural", "--confirm-tags"):
        assert flag in src, f"{module} 缺 {flag}"
    assert "accept_structural=args.confirm_structural" in src, f"{module} 的开关没接进 plan()"


# ── 采纳不等于放宽安全：写类仍然默认排除 ──────────────────────
def test_structural_adoption_does_not_smuggle_write_faces() -> None:
    """结构采纳只动"哪些面有标签"，不动"哪些 oracle 默认不跑"。"""
    from hos_ares_cli.cmd_run import DEFAULT_EXCLUDE

    assert "mass_assignment" in DEFAULT_EXCLUDE
    assert "xss_stored_probe" in DEFAULT_EXCLUDE
