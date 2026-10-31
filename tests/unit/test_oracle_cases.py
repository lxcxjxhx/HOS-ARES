"""B5 用例矩阵驱动测试（``veripen_oracles/cases/tierA.json``）。

07 §0.5 的要求：正样本 ≥4 条且覆盖三种难度；负样本 ≥2 条且含"看似漏洞实则安全"
与"环境退化"两类。这里把矩阵当数据跑，用例不写在代码里。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import Lab

from veripen_oracles import tierA
from veripen_oracles.common.harness import run
from veripen_core.models import Verdict

CASES_PATH = Path(__file__).resolve().parents[2] / "veripen_oracles" / "cases" / "tierA.json"
CASES: dict = json.loads(CASES_PATH.read_text(encoding="utf-8"))

LABS = {
    "vulnerable": Lab(secure=False),
    "hardened": Lab(secure=True),
    "degenerate": Lab(secure=False),
}
LABS["degenerate"].app.degenerate = True

TAG = {
    "sqli_bool_diff": "param_injectable",
    "idor_cross_account": "object_read",
    "ssrf_oob": "url_fetch",
    "jwt_replay": "protected_read",
}

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


@pytest.mark.parametrize(
    ("oracle_id", "kind", "case"),
    ENTRIES,
    ids=[f"{o}-{k}-{c['case']}" for o, k, c in ENTRIES],
)
def test_case_matrix(oracle_id: str, kind: str, case: dict) -> None:
    lab = LABS[case.get("lab", "vulnerable" if kind == "positive" else "hardened")]
    params = dict(case.get("params", {}))
    nonce = params.pop("nonce", "")
    repeat = params.pop("repeat", None)
    oracle = tierA.by_id(oracle_id, **params)
    result = run(
        oracle,
        lab.binding(TAG[oracle_id]),
        lab.transport,
        symbols=lab.symbols,
        nonce=nonce,
        repeat=repeat,
        token=lab.token,
    )
    expected = case["expect"]
    if expected == "VERIFIED":
        assert result.verdict is Verdict.VERIFIED, f"{case['case']}: {result.reason}"
    else:
        assert result.verdict is not Verdict.VERIFIED, f"{case['case']} 误报为 VERIFIED: {result.reason}"
