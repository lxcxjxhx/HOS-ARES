"""B5 用例矩阵驱动测试（``veripen_oracles/cases/tierA.json``）。

07 §0.5 的要求：正样本 ≥4 条且覆盖三种难度；负样本 ≥2 条且含"看似漏洞实则安全"
与"环境退化"两类。这里把矩阵当数据跑，用例不写在代码里。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import Lab

from veripen_oracles import ALL_ORACLES
from veripen_oracles.common.harness import run
from veripen_core.models import Verdict
from veripen.bench.faces import extra_tags, primary_tag

def _by_id(oracle_id: str, **params: object) -> object:
    """按 id 构造（覆盖已注册的全部档位，不只 TierA 前 4 类）。"""
    for cls in ALL_ORACLES:
        if cls.meta.id == oracle_id:
            model = cls.params_model
            return cls(params=model(**params)) if model is not None else cls()
    raise KeyError(oracle_id)


CASES_PATH = Path(__file__).resolve().parents[2] / "veripen_oracles" / "cases" / "tierA.json"
CASES: dict = json.loads(CASES_PATH.read_text(encoding="utf-8"))

def _lab(kind: str) -> Lab:
    """每次现造：写操作类（mass_assignment）会改 app.state，靶场实例不能跨用例共享。"""
    if kind == "degenerate":
        lab = Lab(secure=False)
        lab.app.degenerate = True
        return lab
    return Lab(secure=(kind == "hardened"))

ENTRIES = [
    (oracle_id, kind, case)
    for oracle_id, block in CASES.items()
    if not oracle_id.startswith("_")
    for kind in ("positive", "negative")
    for case in block[kind]
]


def test_matrix_shape() -> None:
    for oracle_id, block in CASES.items():
        if oracle_id.startswith("_"):
            continue
        assert len(block["positive"]) >= 4, f"{oracle_id} 正样本不足 4 条"
        assert len(block["negative"]) >= 2, f"{oracle_id} 负样本不足 2 条"
        labs = {c.get("lab", "vulnerable") for c in block["negative"]}
        assert "degenerate" in labs, f"{oracle_id} 缺'环境退化'类负样本"


def test_faces_have_no_handwritten_copies() -> None:
    """``EXTRA_TAGS`` / ``TAG`` 只许从 ``selector_tags`` 派生，不许有本地副本。

    这条防的是一次真实发生过的分叉：四处各有一份 ``EXTRA_TAGS``，
    ``scripts/demo.py`` 那份缺 ``xss_stored_probe``。分叉不会红——
    它只会静默改变某条用例的结论（多面 oracle 少一条面 → SymbolMissing → BLOCKED）。

    做法：扫产品与脚本目录，出现「给这些 oracle 手工列 tag」的字典就失败。
    """
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    banned = ("EXTRA_TAGS = {", "TAG = {")
    offenders: list[str] = []
    for path in list(root.glob("scripts/**/*.py")) + list(root.glob("tests/**/*.py")):
        if "__pycache__" in str(path) or path.name == "test_oracle_cases.py":
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for marker in banned:
            if marker in text:
                offenders.append(f"{path.relative_to(root)}: {marker}")
    assert offenders == [], f"出现手工面字典副本（应改用 veripen.bench.faces）：{offenders}"


def test_derived_faces_match_oracle_metadata() -> None:
    """派生结果必须与 oracle 类自己的 ``selector_tags`` 逐项一致。"""
    from veripen.bench.faces import TAGS_BY_ORACLE

    for cls in ALL_ORACLES:
        tags = tuple(cls.meta.selector_tags)
        if tags:
            assert TAGS_BY_ORACLE[cls.meta.id] == tags, cls.meta.id


def test_every_multi_face_oracle_is_covered_somewhere() -> None:
    """凡是有多个 selector 的 oracle，**总要有一处端到端跑过它的第二个面**。

    为什么不能只看 tierA.json 矩阵：矩阵只收 10 类，TierC 的三类由
    ``test_tier_c.py`` 单独覆盖（它自己传 ``extra=(...)``）。把断言写成
    "必须在矩阵里"会误报——`xss_stored_probe` 就被误报过一次，
    而它其实在 ``test_stored_xss_vulnerable`` 里跑得很完整。

    所以判据是「第二面在**任何**端到端测试里出现过」，不是「在哪一个文件里」。
    这条防的是真的缺失：多面 oracle 若第二面从没被传入，那个面等于没验证过。
    """
    from pathlib import Path

    from veripen.bench.faces import extra_tags

    root = Path(__file__).resolve().parents[2]
    corpus = "\n".join(
        p.read_text(encoding="utf-8", errors="replace")
        for p in list(root.glob("tests/**/*.py")) + list(root.glob("veripen_oracles/cases/*.json"))
        if "__pycache__" not in str(p) and p.name != "test_oracle_cases.py"
    )
    uncovered: list[str] = []
    for cls in ALL_ORACLES:
        extras = extra_tags(cls.meta.id)
        if not extras:
            continue
        # 第二面必须作为 tag 字面量出现在某处（run 的 bindings/extra 参数里）
        if not any(f'"{t}"' in corpus for t in extras):
            uncovered.append(f"{cls.meta.id}: {extras}")
    assert uncovered == [], f"多面 oracle 的第二面从未被传入：{uncovered}"


@pytest.mark.parametrize(
    ("oracle_id", "kind", "case"),
    ENTRIES,
    ids=[f"{o}-{k}-{c['case']}" for o, k, c in ENTRIES],
)
def test_case_matrix(oracle_id: str, kind: str, case: dict) -> None:
    lab = _lab(case.get("lab", "vulnerable" if kind == "positive" else "hardened"))
    params = dict(case.get("params", {}))
    nonce = params.pop("nonce", "")
    repeat = params.pop("repeat", None)
    oracle = _by_id(oracle_id, **params)
    tags = (primary_tag(oracle_id), *extra_tags(oracle_id))
    bmap = {t: lab.binding(t) for t in tags}
    result = run(
        oracle,
        bmap[tags[0]],
        lab.transport,
        symbols=lab.symbols,
        nonce=nonce,
        repeat=repeat,
        token=lab.token,
        bindings=bmap,
    )
    expected = case["expect"]
    if expected == "VERIFIED":
        assert result.verdict is Verdict.VERIFIED, f"{case['case']}: {result.reason}"
    else:
        assert result.verdict is not Verdict.VERIFIED, f"{case['case']} 误报为 VERIFIED: {result.reason}"
