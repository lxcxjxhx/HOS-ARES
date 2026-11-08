"""B151：`veripen replay` 必须有一条**能跑**的路，而且不把"重算不了"说成"被推翻"。

修前两条路都是死胡同：
- `--mode offline` 直接拒跑（"本命令只跑 live/dry"）；
- 默认 `--mode live` 也拒跑（"需要真实靶场与 transport 注入"）——于是报告里印的
  `veripen replay --capsule <文件>` 照抄必然 exit=1。

而且 header 类 oracle 的证据（`fingerprint={"absent": true}`）在重判时会落
`assertion_not_reproduced`，读起来像"结论被推翻"，其实只是 replay 算不出来。
"""

from __future__ import annotations

import json

import capsule_fixture as F
from veripen_cli.cmd_replay import main as replay_main
from veripen_core.evidence import AssertionBlock, save_capsule, seal_capsule_doc


def _save(tmp_path, cap):  # noqa: ANN001, ANN202
    return save_capsule(seal_capsule_doc(cap), tmp_path / "caps")


def _run(tmp_path, path):  # noqa: ANN001, ANN202
    return replay_main(["--capsule", str(path), "--mode", "offline",
                        "--out", str(tmp_path / "out")])


def test_offline_mode_runs_and_writes_record(tmp_path, capsys) -> None:  # noqa: ANN001
    path = _save(tmp_path, F.capsule())
    assert _run(tmp_path, path) == 0
    out = capsys.readouterr().out
    assert "state: OK" in out
    record = json.loads((tmp_path / "out" / "replay.json").read_text(encoding="utf-8"))
    assert record["mode"] == "offline"
    assert record["state"] == "OK"
    assert record["fingerprint"], "算过的谓词要留在记录里"


def test_offline_mode_names_unrecomputable_predicates(tmp_path, capsys) -> None:  # noqa: ANN001
    """重算不了 → 退出码 0（命令跑完了），但必须说清楚是哪几个谓词。"""
    cap = F.capsule().model_copy(update={"assertion": AssertionBlock(
        kind="csp_absent",
        statement="响应里没有 CSP 头",
        expected={"has_protection": [1]},
        observed={"probe_has": False},
        fingerprint={"absent": True},
    )})
    path = _save(tmp_path, cap)
    assert _run(tmp_path, path) == 0, "证据不足以复核不是命令失败"
    out = capsys.readouterr().out
    assert "predicate_not_recomputable:absent" in out
    assert "证据不足以复核" in out
    assert "不是「结论被推翻」" in out


def test_offline_mode_fails_on_contradiction(tmp_path, capsys) -> None:  # noqa: ANN001
    """算出来是假 → 退出码 1（证据与结论矛盾，这是真问题）。"""
    cap = F.capsule().model_copy(update={"assertion": AssertionBlock(
        kind="idor_cross_account",
        statement="对照不该被拒",
        expected={},
        observed={"control_status": 403},
        fingerprint={"control_denied": False},
    )})
    path = _save(tmp_path, cap)
    assert _run(tmp_path, path) == 1
    assert "assertion_not_reproduced" in capsys.readouterr().out


def test_tampered_capsule_is_refused(tmp_path, capsys) -> None:  # noqa: ANN001
    path = _save(tmp_path, F.capsule())
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["confidence"] = 0.42
    path.write_text(json.dumps(doc), encoding="utf-8")
    assert _run(tmp_path, path) == 1
    assert "TAMPERED" in capsys.readouterr().out


def test_live_mode_explains_what_works_instead(tmp_path, capsys) -> None:  # noqa: ANN001
    """live 仍然 fail-closed——但要说人能去哪儿跑，而不是一句"不行"。"""
    path = _save(tmp_path, F.capsule())
    rc = replay_main(["--capsule", str(path), "--mode", "live", "--out", str(tmp_path / "o")])
    assert rc == 1
    out = capsys.readouterr().out
    assert "--mode offline" in out
    assert "--mode dry" in out
