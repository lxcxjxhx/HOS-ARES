"""B372 · 控制台三处「看得见但说反了」的地方。

三条都是实测撞见的（把控制台指到一份 hunt 产物 + 一个有审批记录的库）：

1. **已判否被读成「跑了但判不了」**——`/ui/unverified` 把 REFUTED 混进
   `inconclusive`，界面上写着「未确认」，而 hunt.md 同一件事写的是「REFUTED（测了，不成立）」。
2. **`/ui/report` 说「还没有报告」**——`REPORT_NAMES` 里没有 `hunt.md`，
   而那份报告就躺在 `hunt.json` 旁边。
3. **`/ui/approvals` 永远 0 条**——库里 56 条审批，页面报 `count: 0, records: true`。

外加一条可移植性：`/ui/finding` 的复现命令贴的是绝对路径，报告那边早就用
`capsules/<sha>.json`——一截图给客户，操作员的目录结构跟着走。
"""

from __future__ import annotations

import json
from pathlib import Path

from web.console import Row, _repro_evidence, unverified_view
from web.loader import load_approvals, load_record, load_report


def _row(*, verdict_outcome: str = "undetermined", refuted: bool, fid: str = "F-1") -> Row:
    return Row(
        finding_id=fid, title="t", severity=None, oracle_id="sqli_bool_diff",
        asset_id="lab", confidence=0.0, route_strength="contract", route_ref=None,
        outcome=verdict_outcome, reason_code="control_also_differs", refuted=refuted,
    )


# ── 1. 已判否 != 跑了但判不了 ──────────────────────────────────
def test_refuted_rows_are_not_filed_under_inconclusive() -> None:
    data = unverified_view([_row(refuted=True), _row(refuted=False, fid="F-2")])
    assert data["by_verdict"]["refuted"] == ["F-1"]
    assert data["by_verdict"]["inconclusive"] == ["F-2"], "真判不了的才进 inconclusive"
    # 旧的三组契约不动（B36），已判否走 by_verdict 的独立一档——前端按 VERDICT_LABEL 迭代
    assert set(data["counts"]) == {"undetermined", "error", "not_run"}


def test_hunt_record_marks_refuted_rows() -> None:
    """hunt.json 的 findings 是 `{oracle: verdict}`：REFUTED 也要带 refuted 位。"""
    record = {
        "target_host": "lab",
        "findings": {"sqli_bool_diff": "VERIFIED", "xss_dom": "REFUTED"},
        "faces_run": [
            {"oracle_id": "sqli_bool_diff", "route_ref": "route://contract/" + "a" * 16,
             "verdict": "VERIFIED", "face_index": 1, "faces_total": 1},
            {"oracle_id": "xss_dom", "route_ref": "route://contract/" + "a" * 16,
             "verdict": "REFUTED", "reason": "marker_not_fired",
             "face_index": 1, "faces_total": 1},
        ],
    }
    rows = {r.oracle_id: r for r in load_record(record).findings}
    assert rows["xss_dom"].refuted is True
    assert rows["xss_dom"].verdict == "refuted"
    assert rows["sqli_bool_diff"].refuted is False


# ── 2. 报告页要认 hunt.md ─────────────────────────────────────
def test_report_page_finds_hunt_markdown(tmp_path: Path) -> None:
    (tmp_path / "hunt.md").write_text("# report\n", encoding="utf-8")
    got = load_report(tmp_path)
    assert got["present"] is True, got
    assert got["name"] == "hunt.md"


# ── 3. 审批页要真读库 ─────────────────────────────────────────
def test_approvals_page_reads_the_store(tmp_path: Path) -> None:
    from veripen_core.store import Store

    db = tmp_path / "x.db"
    store = Store(str(db)).init()
    try:
        store.conn.execute(
            "INSERT INTO approvals (id, task_id, action_class, impact_json, state,"
            " requested_at, decided_at, decided_by, reason) VALUES (?,?,?,?,?,?,?,?,?)",
            ("ap-1", "t", "scope_expand", json.dumps({"assets": "multi"}), "APPROVED",
             1.0, 2.0, "operator", "批量验证放行"),
        )
        store.conn.commit()
        rows = load_approvals(store)
    finally:
        store.close()
    assert [a.id for a in rows] == ["ap-1"]
    assert rows[0].decision == "approved"
    assert rows[0].decided_by == "operator"
    assert "multi" in rows[0].impact


# ── 4. 复现命令不许贴绝对路径 ─────────────────────────────────
def test_repro_path_is_relative() -> None:
    picked = "C:" + chr(92) + "work" + chr(92) + "out" + chr(92) + "capsules" + chr(92) + "abc.json"
    assert _repro_evidence(picked) == "capsules" + chr(0x2F) + "abc.json"
    assert _repro_evidence(None) == ""


def test_console_command_wires_approvals_from_the_db() -> None:
    """页面能显示 ≠ 有人给它装数据：`load_approvals` 必须真的在启动路径上被调用。

    原来的样子就是"函数不存在、页面永远空"，任何只测 page builder 的用例都发现不了。
    """
    src = (Path(__file__).resolve().parents[2] / "veripen_cli" / "cmd_console.py").read_text(
        encoding="utf-8")
    assert "load_approvals(" in src, "审批页没人填数据"
    assert "approvals=approval_rows" in src, "读到了却没装进 state"
