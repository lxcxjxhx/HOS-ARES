"""B150：bench 的证据要**真的落在盘上**，而且每次运行各占一个目录。

两条都在真实网络靶场上验（`run_bench.main` 跑一整轮，约 10s）：

1. 报告说"证据 41 份"时，`capsules/<run>/r1/` 里就是 41 个文件，串成一条能过的链；
2. 同一 `--out` 再跑一次 → 换一个目录，上一轮的 41 份**不混进来**。

第 2 条来自真实事故：capsule 是内容寻址的，同名目录里跑第二次，
旧链和新链混在一个 `r1/` 里——链头有三个、`verify-chain` 全红，
而报告照样写"123 份"。目录不带运行 id，"证据 N 份"就是一句无人核对的话。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
for extra in (ROOT, ROOT / "tests", ROOT / "scripts" / "b12", ROOT / "tests" / "unit"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))


def _bench(out: Path) -> None:
    import run_bench as RB

    rc = RB.main(["--rounds", "1", "--out", str(out)])
    assert rc == 0, "硬门槛没通过的话这条用例本身就没意义"


def _capsule_dir(out: Path, run: Path) -> Path:
    return run / "r1"


def _verify(directory: Path) -> None:
    from veripen_core.evidence import load_capsule, order_chain, verify_chain

    caps = [load_capsule(p) for p in sorted(directory.glob("*.json"))]
    report = verify_chain(order_chain(caps))
    assert report.ok, report.breaks
    assert report.length == len(caps)


def test_each_run_gets_its_own_capsule_dir(tmp_path, capsys) -> None:  # noqa: ANN001
    out = tmp_path / "bench"
    _bench(out)
    first = sorted((out / "capsules").iterdir())
    assert len(first) == 1, f"一次运行应当只有一个证据目录：{first}"
    caps1 = sorted(_capsule_dir(out, first[0]).glob("*.json"))
    assert len(caps1) == 41, f"这一轮 sealed 41 份，盘上却有 {len(caps1)}"
    _verify(_capsule_dir(out, first[0]))

    # 报告里的份数与盘上逐一对得上
    md = (out / "bench.md").read_text(encoding="utf-8")
    assert "证据落盘" in md
    assert first[0].name in md, "报告必须点名本次运行的证据目录"

    _bench(out)
    second = sorted((out / "capsules").iterdir())
    assert len(second) == 2, "第二次运行应当另起目录，而不是往旧目录里堆"
    assert second[0].name != second[1].name
    _verify(_capsule_dir(out, second[0]))
    _verify(_capsule_dir(out, second[1]))
    assert len(list(_capsule_dir(out, second[1]).glob("*.json"))) == 41


@pytest.mark.parametrize("case_id", ["P-23", "P-24", "N-14"])
def test_method_override_is_recorded_and_replayed(case_id: str) -> None:
    """CORS 预检是 OPTIONS：证据必须记 OPTIONS，重放也必须发 OPTIONS。

    B150 之前每一步都写 binding 的 GET，于是预检那一步的证据写着 GET、实际发的是
    OPTIONS；重放照着 GET 打，501 变 200 → PARTIAL。三条 CORS 用例因此永远
    "复现不了"，而那被记成"判定不稳"，其实是证据记错了方法。
    """
    import run_bench as RB
    from veripen.bench import load_cases
    from veripen.bench.capsule import build_capsule, step_params, StepParamsTransport
    from veripen_core.evidence import ReplayEnv, ReplayMode, replay, seal_capsule_doc
    from lab.app import make_token
    from lab.net_server import LabServer
    from lab.net_transport import NetTransport
    from veripen.bench.faces import extra_tags
    from veripen_oracles.oob import LabOob

    case = next(c for c in load_cases(ROOT / "tests" / "bench" / "cases.yaml")
                if c.case_id == case_id)
    app = RB._pick_lab(case)
    srv = LabServer(app).start()
    try:
        rs = RB._routeset(srv.base_url, (case.selector,))
        binding = rs.bindings[0]
        bindings = {case.selector: binding}
        for tag in extra_tags(case.oracle):
            bindings[tag] = RB._routeset(srv.base_url, (tag,)).bindings[0]
        transport = NetTransport(
            token_for=make_token, oob=LabOob(app=app, endpoint_value="oob-SAMPLE")
        )
        result = RB.run_oracle(
            RB._oracle(case.oracle), binding, transport, symbols=RB.SYMBOLS,
            nonce=f"bench-{case_id}", token=make_token("primary"), bindings=bindings,
        )
        cap = seal_capsule_doc(build_capsule(
            case_id=case.case_id, oracle_id=case.oracle, oracle_version="t",
            verdict=result.verdict, judgement=result.judgement, pairs=result.pairs,
            baseline_params=result.baseline_params, routeset=rs,
            route_ref=binding.ref, binding=binding, bindings_by_tag=bindings,
            created_at="2026-11-08T00:00:00Z",
        ))
    finally:
        srv.stop()

    preflight = [s for s in cap.steps if s.request.method == "OPTIONS"]
    assert preflight, "预检那一步的方法必须是 OPTIONS"
    assert json.dumps([s.request.method for s in cap.steps])  # 方法进得了证据

    # 在干净实例上重放：方法按证据发，才有机会复现"预检返回什么"
    app2 = RB._pick_lab(case)
    srv2 = LabServer(app2).start()
    try:
        rs2 = RB._routeset(srv2.base_url, ())
        rt = StepParamsTransport(
            NetTransport(token_for=make_token, oob=LabOob(app=app2, endpoint_value="oob-SAMPLE")),
            step_params(cap, result.baseline_params, result.pairs),
        )
        out = replay(cap, ReplayEnv(now=0, mode=ReplayMode.LIVE, routeset=rs2), rt)
    finally:
        srv2.stop()
    assert out.state.value == "OK", out.reason
