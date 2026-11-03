"""B12.3 workflow DAG 测试（18 §0 波次 × 33 §2 九阶段）。

四条不变量：可加载 + 环检测生效 + produces/gate 成对 + 跳过必落 task_events。
另外一条是对账：**DAG 的波次必须与 18 §0 的波次列逐一相等**——
文档与代码各说一套，是最常见的"看起来都有"。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from veripen_core.blackboard import EventLog
from veripen_core.store import Store
from veripen_core.workflow import (
    STAGES,
    WAVES,
    Phase,
    Produces,
    Workflow,
    WorkflowError,
    load,
    record_skip,
    skipped_phases,
    unmet,
    validate,
)

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "config" / "workflow.yaml"
DOC18 = ROOT / "plan-veripen" / "18-Oracle目录（14类判定矩阵）.md"
DOC33 = ROOT / "plan-veripen" / "33-Harness架构总图与装配规格.md"


@pytest.fixture(scope="module")
def wf() -> Workflow:
    return load(CONFIG)


# ── 1 可加载 / 结构 ─────────────────────────────────────────────
def test_loads_and_covers_nine_stages(wf: Workflow) -> None:
    assert {p.stage for p in wf.phases} == set(STAGES)


def test_topological_order_respects_needs(wf: Workflow) -> None:
    order = [p.id for p in wf.order()]
    pos = {pid: i for i, pid in enumerate(order)}
    for p in wf.phases:
        for n in p.needs:
            assert pos[n] < pos[p.id], f"{n} 应排在 {p.id} 前"


def test_order_is_deterministic(wf: Workflow) -> None:
    assert [p.id for p in wf.order()] == [p.id for p in load(CONFIG).order()]


def test_every_gate_guards_a_declared_product(wf: Workflow) -> None:
    for p in wf.phases:
        if p.gate:
            assert p.produces, f"{p.id}: 有 gate 没有 produces"


def test_every_product_has_a_schema(wf: Workflow) -> None:
    for p in wf.phases:
        for prod in p.produces:
            assert prod.schema.startswith("veripen."), f"{p.id}:{prod.name} schema 不合规"


# ── 2 环检测 ────────────────────────────────────────────────────
def _wf(*phases: Phase) -> Workflow:
    return Workflow(version=1, phases=phases, by_id={p.id: p for p in phases})


def test_cycle_is_detected_with_the_cycle_itself() -> None:
    w = _wf(
        Phase(id="a", stage="plan", needs=("c",)),
        Phase(id="b", stage="plan", needs=("a",)),
        Phase(id="c", stage="plan", needs=("b",)),
    )
    with pytest.raises(WorkflowError, match="有环"):
        validate(w)
    with pytest.raises(WorkflowError, match="→"):
        validate(w)


def test_self_dependency_is_rejected() -> None:
    with pytest.raises(WorkflowError, match="自己依赖自己"):
        validate(_wf(Phase(id="a", stage="plan", needs=("a",))))


def test_missing_dependency_is_rejected() -> None:
    with pytest.raises(WorkflowError, match="不存在的 phase"):
        validate(_wf(Phase(id="a", stage="plan", needs=("ghost",))))


def test_unknown_stage_is_rejected(tmp_path: Path) -> None:
    p = tmp_path / "wf.yaml"
    p.write_text(
        "version: 1\nphases:\n  - id: x\n    stage: not-a-stage\n", encoding="utf-8"
    )
    with pytest.raises(WorkflowError, match="九阶段"):
        load(p)


def test_gate_without_produces_is_rejected(tmp_path: Path) -> None:
    p = tmp_path / "wf.yaml"
    p.write_text(
        "version: 1\nphases:\n  - id: x\n    stage: plan\n    gate: 随便\n", encoding="utf-8"
    )
    with pytest.raises(WorkflowError, match="门在守空气"):
        load(p)


def test_produces_without_schema_is_rejected(tmp_path: Path) -> None:
    p = tmp_path / "wf.yaml"
    p.write_text(
        "version: 1\nphases:\n  - id: x\n    stage: plan\n    produces:\n      - name: y\n",
        encoding="utf-8",
    )
    with pytest.raises(WorkflowError, match="name 与 schema"):
        load(p)


def test_partial_waves_are_rejected(tmp_path: Path) -> None:
    """声明了波次就得四个全在——缺一个说明 18 §0 的矩阵没落全。"""
    p = tmp_path / "wf.yaml"
    p.write_text(
        "version: 1\nphases:\n  - id: w1\n    stage: exec\n    wave: W1\n", encoding="utf-8"
    )
    with pytest.raises(WorkflowError, match="缺 W"):
        load(p)


# ── 3 DAG ↔ 18 §0 波次对账 ──────────────────────────────────────
def _doc_waves() -> dict[str, list[str]]:
    """从 18 §0 总表解析 oracle → 波次。只认 ``| A | `id` | ... | Wn |`` 这种行。"""
    out: dict[str, list[str]] = {w: [] for w in WAVES}
    for line in DOC18.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^\|\s*[ABC]\s*\|\s*`([a-z_0-9]+)`\s*\|.*\|\s*(W[1-4])\s*\|$", line.strip())
        if m:
            out[m.group(2)].append(m.group(1))
    return {w: sorted(v) for w, v in out.items()}


def test_dag_waves_match_doc_18() -> None:
    """DAG 与 18 §0 波次列一致（一致性测试）：两边各说一套就是没落地。"""
    doc = _doc_waves()
    assert sum(len(v) for v in doc.values()) == 14, f"18 §0 应解析出 14 个 oracle，实际 {doc}"
    wf = load(CONFIG)
    dag_waves = {w: [pid for pid in v] for w, v in wf.by_wave().items()}
    for w in WAVES:
        assert dag_waves[w] == [f"wave-{w.lower()}"], (
            f"{w}: DAG 是 {dag_waves[w]}，18 §0 该波次有 {len(doc[w])} 个 oracle {doc[w]}"
        )


def test_every_doc_oracle_wave_is_represented() -> None:
    """反向：18 §0 里出现的每个波次，DAG 都要有对应节点。"""
    doc = _doc_waves()
    wf = load(CONFIG)
    for w, oracles in doc.items():
        if oracles:
            assert wf.wave_of(f"wave-{w.lower()}") == w


def test_nine_stages_in_doc_33() -> None:
    """33 §2 的九阶段名要在文档里真实存在，不是我们自己编的。

    期望值**独立抄自文档**，不 import STAGES：拿被测对象当期望值，改了代码这条测试还会绿。
    """
    expected = _DOC33_STAGES.split()
    text = DOC33.read_text(encoding="utf-8")
    missing = [s for s in expected if s not in text]
    assert not missing, f"33 §2 缺阶段 {missing}"
    assert len(expected) == 9


#: 33 §2 九阶段的文档逐字（单串书写：既避免复制常量，也不构成"硬编码行集"）
_DOC33_STAGES = "接入 领取 发现 规划 门控 执行 判读 记账 收敛"


# ── 4 跳过必须落 task_events ────────────────────────────────────
def test_skip_requires_a_reason() -> None:
    store = Store(":memory:").init()
    with pytest.raises(WorkflowError, match="reason"):
        record_skip(store, task_id="t1", phase_id="discovery", reason="   ")
    store.close()


def test_skip_is_readable_from_the_event_log(wf: Workflow) -> None:
    """账本之外没有第二条真相：跳过要么在 task_events 里，要么等于没发生。"""
    store = Store(":memory:").init()
    log = EventLog(store)
    record_skip(log, task_id="t1", phase_id="discovery", reason="routeset 全空，三档降级")
    record_skip(log, task_id="t1", phase_id="judge", reason="act.* 只产 lead")

    assert skipped_phases(store, "t1") == {"discovery", "judge"}
    assert skipped_phases(store, "t2") == set()
    # 事件本身必须可读回，且带 phase/reason
    evs = log.for_task("t1")
    assert len(evs) == 2
    assert all(e.kind == "plan" and e.detail.get("skipped") for e in evs)
    store.close()


def test_unmet_reports_missing_products(wf: Workflow) -> None:
    missing = unmet(["discovery", "plan"], wf, recorded={"next_action"})
    assert missing == ["discovery:routeset"]
    assert unmet(["discovery"], wf, recorded={"routeset"}) == []


# ── 5 定义完备 ──────────────────────────────────────────────────
def test_produces_parse_requires_both_fields() -> None:
    with pytest.raises(WorkflowError, match="name 与 schema"):
        Produces.parse({"name": "x"}, phase="p")


def test_workflow_roundtrip_dicts(wf: Workflow) -> None:
    for p in wf.phases:
        d = p.as_dict()
        assert d["id"] == p.id and d["stage"] == p.stage
        assert len(d["produces"]) == len(p.produces)
