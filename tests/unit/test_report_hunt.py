"""B166：`hunt.md` 与 `hunt.json` 同源——不许出现"两个出口两个说法"。

报告是给人看的，JSON 是给机器看的，两者必须来自同一个 dict。
这组用例只钉四件事：

1. 结论表只列 VERIFIED，且每行都指得到证据；
2. 未确认行必须带原因（D1）；
3. 证据段的命令指向真实路径（`<dir>/<sha>.json`），不是 `sha256:<hex>.json`；
4. "完整：否"必须给原因（提前停 vs 有缺口，读者要能分清）。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for extra in (ROOT, ROOT / "tests", ROOT / "tests" / "unit"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

SHA = "a" * 64
CID = "sha256:" + SHA


def _record(**kw):  # noqa: ANN202
    base = {
        "target_host": "lab",
        "source": "contract",
        "decision": "converged",
        "reason": "finished",
        "findings": {"sqli_bool_diff": "VERIFIED"},
        "not_confirmed": {"xss_dom": {"verdict": "REFUTED", "reason": "marker_not_fired"}},
        "faces_run": [
            {"oracle_id": "sqli_bool_diff", "route_ref": "route://contract/" + "b" * 16,
             "verdict": "VERIFIED", "reason": "", "face_index": 1, "faces_total": 2},
            {"oracle_id": "xss_dom", "route_ref": "route://contract/" + "b" * 16,
             "verdict": "REFUTED", "reason": "marker_not_fired", "face_index": 2, "faces_total": 2},
        ],
        "coverage": {
            "summary": {"faces": 2, "faces_covered": 2, "applicable_pairs": 4,
                        "executed": 4, "gaps": 0},
            "gaps": 0, "complete": True, "caveats": [],
        },
        "capsules": [SHA],
        "evidence": [{"oracle": "sqli_bool_diff",
                      "route_ref": "route://contract/" + "b" * 16, "capsule_id": CID}],
        "agent_claim": {"summary": "", "unresolved": []},
    }
    base.update(kw)
    return base


def _render(record, **kw):  # noqa: ANN001, ANN003
    from veripen.report.hunt import render_hunt_markdown

    kw.setdefault("capsules_dir", "out/x/capsules")
    kw.setdefault("generated_at", "2026-11-09T00:00:00+0800")
    return render_hunt_markdown(record, **kw)


def test_confirmed_rows_carry_evidence_and_unconfirmed_rows_carry_reason() -> None:
    md = _render(_record())
    assert "# 渗透验证报告 · lab" in md
    confirmed = [line for line in md.splitlines()
                 if line.startswith("| `sqli_bool_diff`") and "VERIFIED" in line]
    assert confirmed and CID in confirmed[0], "确认行必须指得到证据"
    unconfirmed = [line for line in md.splitlines() if line.startswith("| `xss_dom`")]
    assert unconfirmed and "marker_not_fired" in unconfirmed[0], "未确认必须带原因"


def test_evidence_commands_point_at_real_paths() -> None:
    md = _render(_record())
    assert f"veripen replay --capsule out/x/capsules/{SHA}.json --mode offline" in md
    assert f"sha256:{SHA}.json" not in md, "把 capsule id 当文件名（B149 的老坑）"


def test_verified_without_evidence_is_marked_not_invented() -> None:
    """VERIFIED 但没有证据：证据列写 `-`，不许编一个 id。"""
    md = _render(_record(evidence=[], capsules=[]))
    assert "（本轮没有落任何 capsule" in md
    row = next(line for line in md.splitlines() if line.startswith("| `sqli_bool_diff`"))
    assert row.rstrip().endswith("| - |"), row


def test_incomplete_coverage_explains_why() -> None:
    md = _render(_record(
        decision="budget", reason="budget_exhausted:steps",
        coverage={"summary": {"faces": 1, "faces_covered": 1, "applicable_pairs": 3,
                              "executed": 3, "gaps": 0},
                  "gaps": 0, "complete": False,
                  "caveats": ["只跑了 1 步就停了"]},
    ))
    assert "**覆盖不完整**" in md
    assert "budget_exhausted:steps" in md, "不完整必须说清是提前停，而不是有面没跑"
    assert "只跑了 1 步就停了" in md


def test_agent_claim_is_quoted_but_marked_not_a_verdict() -> None:
    md = _render(_record(agent_claim={"summary": "找到 1 条", "unresolved": ["rate_limit"]}))
    section = md.split("## 4. 模型自述（不计入判定）", 1)[1]
    assert "找到 1 条" in section and "rate_limit" in section


def test_report_never_prints_the_full_route_ref() -> None:
    """路由引用只给尾 8 位：报告要能贴给客户。"""
    md = _render(_record())
    assert "route://contract/" not in md
