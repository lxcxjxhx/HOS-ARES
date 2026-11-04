"""B50 Playbook 资产（`H03 §2` / `H07` B50）。

判据：schema 通过 · oracle/impact 一致 · 成功率必须等于实测且样本 ≥5 ·
三个资产齐备（含一个**负数**：SQLi 在夹具上不成立 → 0.0，照写不美化）。
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
import yaml

from veripen_cli.cmd_playbook import oracle_table
from veripen_core.playbooks import (
    MEASURED_PATH,
    PLAYBOOK_DIR,
    PlaybookError,
    check_success_rates,
    load_all,
    load_dir,
    load_measured,
    validate_against_oracles,
)

ROOT = Path(__file__).resolve().parents[2]


def _copy_books(tmp_path: Path) -> Path:
    target = tmp_path / "playbooks"
    target.mkdir()
    for path in PLAYBOOK_DIR.glob("*.yaml"):
        shutil.copy(path, target / path.name)
    return target


# ── 正例 ───────────────────────────────────────────────────────
def test_repo_playbooks_pass_all_checks() -> None:
    books = load_all(oracle_table=oracle_table())
    assert {b.id for b in books} == {
        "pb-idor-enum-first", "pb-sqli-waf-probe", "pb-ratelimit-window"}


def test_success_rates_match_measured_numbers() -> None:
    measured = load_measured()
    assert set(measured) == {b.id for b in load_dir()}
    for book in load_dir():
        rec = measured[book.id]
        assert rec["samples"] >= 5
        assert book.success_rate == pytest.approx(rec["success_rate"]), "手改的数和实测不一致"
        assert rec.get("rehearsal") is True, "必须标明这是演练数，不是真实资产数"


def test_known_oracles_cover_playbook_oracles() -> None:
    table = oracle_table()
    assert {"idor_cross_account", "sqli_bool_diff", "rate_limit_absent"} <= set(table)


def test_playbooks_have_pitfalls_and_no_rollback() -> None:
    for book in load_dir():
        assert len(book.data["pitfalls"]) >= 2, f"{book.id} 缺失败模式"
        assert book.data["rollback"] == "none", "read_only 打法不该有回滚"


# ── 反例：每一条都能红 ─────────────────────────────────────────
def test_hand_edited_rate_is_caught(tmp_path: Path) -> None:
    books = load_dir()
    measured = load_measured()
    measured[books[0].id] = {**measured[books[0].id], "success_rate": 0.1234}
    with pytest.raises(PlaybookError) as err:
        check_success_rates(books, measured)
    assert err.value.code == "rate_mismatch"


def test_missing_measurement_for_nonnull_rate_is_caught(tmp_path: Path) -> None:
    books = load_dir()
    with pytest.raises(PlaybookError) as err:
        check_success_rates(books, {})
    assert err.value.code == "unmeasured_rate"


def test_insufficient_samples_is_caught() -> None:
    books = load_dir()
    measured = {b.id: {"success_rate": b.success_rate or 0.0, "samples": 2} for b in books}
    with pytest.raises(PlaybookError) as err:
        check_success_rates(books, measured)
    assert err.value.code == "insufficient_samples"


def test_schema_missing_field_is_caught(tmp_path: Path) -> None:
    target = _copy_books(tmp_path)
    path = next(target.glob("*.yaml"))
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data.pop("rollback")
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    with pytest.raises(PlaybookError) as err:
        load_dir(target)
    assert err.value.code == "schema_invalid"


def test_unknown_oracle_is_caught(tmp_path: Path) -> None:
    target = _copy_books(tmp_path)
    path = next(target.glob("*.yaml"))
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["oracle"] = "not_an_oracle"
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    with pytest.raises(PlaybookError) as err:
        load_all(target, measured={}, oracle_table=oracle_table())
    # schema 先过（pattern 合法），到 oracle 检查才炸
    assert err.value.code in {"unknown_oracle"}


def test_impact_mismatch_is_caught(tmp_path: Path) -> None:
    target = _copy_books(tmp_path)
    path = target / "pb-idor-enum-first.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["impact"] = "reversible_write"
    data["rollback"] = {"action": "manual"}
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    with pytest.raises(PlaybookError) as err:
        load_all(target, measured={}, oracle_table=oracle_table())
    assert err.value.code == "impact_mismatch"


def test_duplicate_id_is_caught(tmp_path: Path) -> None:
    target = _copy_books(tmp_path)
    src = target / "pb-idor-enum-first.yaml"
    shutil.copy(src, target / "pb-idor-enum-first-copy.yaml")
    with pytest.raises(PlaybookError) as err:
        load_dir(target)
    assert err.value.code == "duplicate_id"


def test_measured_file_is_declared_as_rehearsal() -> None:
    raw = json.loads(MEASURED_PATH.read_text(encoding="utf-8"))
    assert all(row.get("fixture") == "in-process" for row in raw.values())
    assert ROOT.joinpath("playbooks") == PLAYBOOK_DIR
