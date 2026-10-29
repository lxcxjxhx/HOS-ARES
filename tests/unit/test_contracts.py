"""契约测试（`01 §7`）。这三条通过 = 架构没走样，比功能测试更重要。

B1 的 DoD：`pytest tests/unit/test_contracts.py` 3 绿。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from veripen_core import (
    Asset,
    HttpTrace,
    ImpactClass,
    Judgement,
    Oracle,
    RequestPair,
    TargetContext,
    Variant,
    Verdict,
    canonical_json,
    three_condition_judge,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
CORE_DIR = REPO_ROOT / "veripen_core"


def read_all_source(pkg_dir: Path) -> str:
    """拼接包内全部 Python 源码（用于静态禁令检查）。"""
    chunks: list[str] = []
    for path in sorted(pkg_dir.rglob("*.py")):
        chunks.append(path.read_text(encoding="utf-8"))
    return "\n".join(chunks)


def test_core_does_not_import_oracles() -> None:
    """`core` 不 import `oracles`（`01 §1` 依赖铁律 #1）。

    用 AST 查真正的 import 语句，而不是子串匹配——
    否则注释里提到 `veripen_oracles` 就会误报。
    """
    offenders: list[str] = []
    for path in sorted(CORE_DIR.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.split(".")[0] == "veripen_oracles":
                        offenders.append(f"{path.name}: import {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                if mod.split(".")[0] == "veripen_oracles":
                    offenders.append(f"{path.name}: from {mod} import ...")
    assert offenders == [], f"core must not import oracles (01 §1): {offenders}"


class _StubOracle:
    """一个最简 oracle 实现，用于验证协议本身可被满足且 judge 是纯的。

    它只调 `three_condition_judge`——这也是所有真实 oracle 的强制路径。
    """

    id = "stub_oracle"
    version = "1.0.0"
    cwe = "CWE-89"
    impact = ImpactClass.READ_ONLY
    requires: list[str] = []
    params_schema = type("P", (), {})

    def applicable(self, ctx: TargetContext) -> bool:  # noqa: ARG002 — 协议要求
        return True

    def plan(self, ctx: TargetContext, baseline: HttpTrace) -> list[Variant]:  # noqa: ARG002
        return [Variant(variant="bool_true", method="GET", url=ctx.base_url, expected_differential=True)]

    def judge(self, pairs: list[RequestPair]) -> Judgement:
        # 纯函数：只读入参，不碰网络/时钟/随机。
        match = any(p.probe.body_len != p.baseline.body_len for p in pairs)
        return three_condition_judge(
            probe_matches=match,
            baseline_normal=all(p.baseline.status == 200 for p in pairs),
            control_expected=all(p.baseline.status == 200 for p in pairs),
            reason_code="stub",
            assertion={"kind": "differential"},
        )


def test_judge_is_pure() -> None:
    """同一 pairs 输入调用 100 次，verdict 集合只有一个元素（`01 §7`）。

    这条测的是"判定与时钟/随机/IO 无关"——它是 A8（模型不是瓶颈）的地基。
    """
    oracle = _StubOracle()
    assert isinstance(oracle, Oracle), "stub must satisfy the Oracle protocol"

    pairs = [
        RequestPair(
            variant="bool_true",
            baseline=HttpTrace(method="GET", url="http://x/", status=200, body_len=100),
            probe=HttpTrace(method="GET", url="http://x/", status=200, body_len=900),
        ),
        RequestPair(
            variant="control",
            baseline=HttpTrace(method="GET", url="http://x/", status=200, body_len=100),
            probe=HttpTrace(method="GET", url="http://x/", status=200, body_len=100),
        ),
    ]

    verdicts = {oracle.judge(pairs).verdict for _ in range(100)}
    assert verdicts == {Verdict.VERIFIED}, f"judge must be deterministic, got {verdicts}"

    # 同样地，三次合取缺一即不得 VERIFIED —— 这是 FP=0 的机械保证。
    assert three_condition_judge(
        probe_matches=True, baseline_normal=False, control_expected=True,
        reason_code="t", assertion={},
    ).verdict is Verdict.INCONCLUSIVE
    assert three_condition_judge(
        probe_matches=True, baseline_normal=True, control_expected=False,
        reason_code="t", assertion={},
    ).verdict is Verdict.INCONCLUSIVE
    assert three_condition_judge(
        probe_matches=False, baseline_normal=True, control_expected=True,
        reason_code="t", assertion={},
    ).verdict is Verdict.REFUTED


def test_capsule_is_content_addressed() -> None:
    """同输入两次 seal 同哈希；键序不同也同哈希（`01 §7`、`06 §2`）。"""
    a = {"b": 1, "a": [3, 2, 1], "c": {"z": 1.0000001, "y": "x"}}
    b = {"c": {"y": "x", "z": 1.0000001}, "a": [3, 2, 1], "b": 1}
    assert canonical_json(a) == canonical_json(b)
    assert canonical_json(a) == canonical_json(a)

    # 浮点规范化到 6 位：1.0000001 与 1.0 应产生相同规范化结果
    from veripen_core import sha256_of

    assert sha256_of(a) == sha256_of(b)
    assert sha256_of({"v": 1.0000001}) == sha256_of({"v": 1.0})
    assert sha256_of(a) != sha256_of({**a, "b": 2}), "different content must differ"


def test_request_pair_is_immutable() -> None:
    """`RequestPair` 是不可变 dataclass：oracle 不能就地改输入再判定。"""
    pair = RequestPair(
        variant="bool_true",
        baseline=HttpTrace(method="GET", url="http://x/", status=200),
        probe=HttpTrace(method="GET", url="http://x/", status=200),
    )
    with pytest.raises(Exception):
        pair.variant = "mutated"  # type: ignore[misc]

    asset = Asset(id="web-01", kind="url", value="http://dvap.local", criticality="medium", tags=("web",))
    with pytest.raises(Exception):
        asset.id = "other"  # type: ignore[misc]
