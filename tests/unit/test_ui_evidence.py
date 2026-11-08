"""B157：控制台给出的复现命令必须**跑得通**。

修前 `ui.finding` 的 `repro` 是 `make replay ID=<finding_id>`，而 `make replay`
要的是 **capsule 文件路径**——照抄必然 `capsule 载入失败`。跟 B149（报告里拼错
文件名）、B151（默认 live 拒跑）是同一类：命令看着像命令，拷出去是死链。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
for extra in (ROOT, ROOT / "tests", ROOT / "tests" / "unit"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

SEP = chr(0x2F)
UI = SEP + "ui" + SEP


def _run_dir(tmp_path: Path, *, with_capsule: bool) -> tuple[Path, str]:
    """造一个最小的 run 产物：findings.json + （可选）capsules/<sha>.json。"""
    import capsule_fixture as F

    from veripen_core.evidence import save_capsule, seal_capsule_doc

    run = tmp_path / "run"
    run.mkdir()
    cap = seal_capsule_doc(F.capsule())
    cid = cap.capsule_id
    if with_capsule:
        save_capsule(cap, run / "capsules")
    findings = {
        "schema": "veripen.report/v1",
        "engagement": "lab",
        "root_sha256": cap.chain.self_sha256,
        "counts": {"total": 1, "by_outcome": {"reported": 1}},
        "findings": [
            {
                "id": "F-idor_cross_account-aaaaaaaa",
                "oracle": "idor_cross_account",
                "title": "对象级授权缺失",
                "severity": "high",
                "confidence": 0.95,
                "asset": "lab",
                "route": {"ref": "route://contract/" + "a" * 16, "strength": "contract"},
                "evidence": {"capsule_id": cid, "self_sha256": cap.chain.self_sha256,
                             "replay_status": "NEVER"},
                "outcome": "reported",
                "reason_code": "",
            }
        ],
    }
    (run / "findings.json").write_text(json.dumps(findings), encoding="utf-8")
    return run, cid


def _client(run: Path) -> TestClient:
    from web import build_app
    from web.loader import load_file

    return TestClient(build_app(load_file(str(run))))


def test_repro_points_at_a_real_capsule_file(tmp_path: Path) -> None:
    from veripen_core.evidence import load_capsule

    run, cid = _run_dir(tmp_path, with_capsule=True)
    data = _client(run).get(UI + "finding?id=F-idor_cross_account-aaaaaaaa").json()["data"]

    assert data["capsule_present"] is True
    assert data["capsule_path"], "loader 没解析出证据文件路径"
    assert data["repro_ok"] is True
    assert data["repro"] == f"veripen replay --capsule {data['capsule_path']} --mode offline"
    assert "make replay" not in data["repro"], "旧命令把 finding_id 当 capsule 路径"

    # 命令里那条路径真的能载入，且是同一份证据
    assert load_capsule(data["capsule_path"]).capsule_id == cid


def test_missing_capsule_file_means_no_command(tmp_path: Path) -> None:
    """指针在、文件不在 → 不给命令，也不假装能复现。"""
    run, _cid = _run_dir(tmp_path, with_capsule=False)
    data = _client(run).get(UI + "finding").json()["data"]

    assert data["capsule_present"] is True, "结论里有指针"
    assert data["capsule_path"] is None, "盘上没有这份文件"
    assert data["repro"] == ""
    assert data["repro_ok"] is False


def test_console_does_not_run_replay_by_itself(tmp_path: Path) -> None:
    """控制台是只读视图：它给命令，不替人执行（断言只依赖文件存在）。"""
    run, _cid = _run_dir(tmp_path, with_capsule=True)
    data = _client(run).get(UI + "finding").json()["data"]
    assert data["replay"] == "NEVER", "产物里没跑过重放，界面不许替它填一个状态"


@pytest.mark.parametrize("op", ["finding", "findings"])
def test_pages_still_answer_without_artifacts(op: str) -> None:
    """空目录 → 空状态，不崩（B37 的纪律在新字段上同样成立）。"""
    from web import ConsoleState, build_app

    resp = TestClient(build_app(ConsoleState())).get(UI + op)
    assert resp.status_code == 200
