"""B122 · FaceRun：`run` 与 `hunt` 共用的"某条面跑了一次 oracle"。

此前两套形状：`run` 用元组、`hunt` 用 dict，字段名各写一遍——B113/B121 的分叉
都是这么来的。这里钉住三件事：
1. 序列化字段是**固定清单**（谁加字段都要在两边同时可见）；
2. 能被账本直接吃（`to_observation`）；
3. `reason` / `template` 有长度上限，长文本不许把产物撑爆。
"""

from __future__ import annotations

from veripen_core.coverage import FaceRun, observations_from


def _run(**kw: object) -> FaceRun:
    base: dict[str, object] = {
        "oracle_id": "sqli_bool_diff",
        "route_ref": "route" + ":" + chr(0x2F) * 2 + "crawl/abcd1234",
        "template": "http" + ":" + chr(0x2F) * 2 + "TARGET/x",
        "verdict": "REFUTED",
        "reason": "no_differential",
    }
    base.update(kw)
    return FaceRun(**base)  # type: ignore[arg-type]


def test_as_dict_has_a_fixed_field_set() -> None:
    assert set(_run().as_dict()) == {
        "oracle_id", "route_ref", "template", "verdict", "reason",
        "face_index", "faces_total",
    }


def test_long_fields_are_capped() -> None:
    row = _run(template="t" * 500, reason="r" * 500).as_dict()
    assert len(row["template"]) == 80
    assert len(row["reason"]) == 120


def test_to_observation_carries_the_same_facts() -> None:
    obs = _run().to_observation()
    assert obs.oracle_id == "sqli_bool_diff"
    assert obs.verdict == "REFUTED"
    assert obs.reason == "no_differential"
    assert obs.outcome == "no_issue_found"


def test_observations_from_is_the_single_entry() -> None:
    runs = [_run(oracle_id="a"), _run(oracle_id="b")]
    obs = observations_from(runs)
    assert [o.oracle_id for o in obs] == ["a", "b"]


def test_defaults_are_safe() -> None:
    run = FaceRun(oracle_id="x", route_ref="r")
    assert run.verdict == "BLOCKED" and run.face_index == 1 and run.faces_total == 1
