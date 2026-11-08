"""B152：工具结果里的 `capsule_id` 不许是编的。

`run` / `bench` 会真的 seal 并写盘，所以它们给的 id 指向盘上的文件；
`hunt` 和两个演示 runtime 不封 capsule，却在结果里回了一个
`f"sha256:{oracle_id}"`——形如 `sha256:csp_absent` 的假号。

这条最坏的地方是它的去处：工具结果会进**模型上下文**
（`veripen_core/tools/registry.py` → `ToolResult.data`），模型会把它当证据引用；
报告读者照着去 `out/capsules/` 找，什么也没有。

修法不是"少说点"，而是说清楚：`capsule_id=None` + `evidence="not_sealed"`。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
for extra in (ROOT, ROOT / "tests", ROOT / "tests" / "unit"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

SEP = chr(0x2F)
LOOPBACK = "127." + "0.0.1"


def _routeset(faces: int = 1):  # noqa: ANN202
    """n 条 crawl 面：本用例不打网络，`run_oracle` 会被替身接管。"""
    import time

    from veripen_core.routes import (
        AssetRef, Resolver, RouteCandidate, RouteHints, RouteProvenance, RouteQuery,
    )
    from veripen_core.routes.sources import StaticSource

    cands = [
        RouteCandidate(
            method="GET",
            template=SEP + chr(ord("a") + i),
            param_names=("x",),
            source=RouteProvenance.CRAWL,
            response_shape={"semantic_tags": ["param_injectable"]},
        )
        for i in range(faces)
    ]
    return Resolver(sources=[StaticSource("crawl", RouteProvenance.CRAWL, cands)]).resolve(
        RouteQuery(asset=AssetRef(id=LOOPBACK), hints=RouteHints(), min_confidence=0.0),
        now=int(time.time()),
    )


class _TransportStub:
    oracle_id = ""


def _verified(oracle, binding, transport, **kw):  # noqa: ANN001, ANN003, ANN202
    """替身只负责"判出 VERIFIED"，证据构造由 `build_capsule` 的替身接管。"""
    from veripen_core.models import Verdict
    from veripen_oracles.common.harness import RunResult

    return RunResult(Verdict.VERIFIED, "verified_for_test", object(), (object(),), {})


def test_hunt_tool_result_has_no_fake_capsule(monkeypatch) -> None:
    """没接证据目录 → 结果里必须说 `not_sealed`，而不是编一个 sha256 号。"""
    from veripen_cli import cmd_hunt

    monkeypatch.setattr(cmd_hunt, "run_oracle", _verified)
    rt = cmd_hunt.HuntRuntime(_routeset(), transport=_TransportStub(),
                              symbols=__import__("veripen_oracles").SymbolTable())
    out = rt.run_oracle("sqli_bool_diff", "", None)

    assert out["verdict"] == "VERIFIED"
    assert out["capsule_id"] is None, f"编了证据指针：{out.get('capsule_id')!r}"
    assert out["evidence"] == "not_sealed"
    assert "没有可重放证据" in out["evidence_note"]


def test_hunt_seals_verified_results_into_one_chain(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    """接了证据目录 → 每个 VERIFIED 真的落一份，且**增量串链**（id 是终值）。

    增量封印是关键：`prev` 用上一条的 `self`，返回给模型的 id 因此不会再变，
    不需要 run/bench 那种"封印后回填"（B148/B150 的坑）。
    """
    import capsule_fixture as F

    from veripen_cli import cmd_hunt
    from veripen_core.evidence import ZERO_SHA, load_capsule

    monkeypatch.setattr(cmd_hunt, "build_capsule",
                        lambda **kw: F.capsule(probe_body=str(kw["case_id"])))
    monkeypatch.setattr(cmd_hunt, "run_oracle", _verified)

    capsules = tmp_path / "capsules"
    rt = cmd_hunt.HuntRuntime(_routeset(2), _TransportStub(),
                              __import__("veripen_oracles").SymbolTable(),
                              capsules_dir=capsules)
    first = rt.run_oracle("sqli_bool_diff", "", None)
    second = rt.run_oracle("sqli_bool_diff", "", None)

    assert first["evidence"] == second["evidence"] == "sealed"
    assert first["capsule_id"] and second["capsule_id"]
    assert first["capsule_id"] != second["capsule_id"], "两个面不能拿到同一份证据"

    files = sorted(capsules.glob("*.json"))
    assert len(files) == 2, f"应当落两份，实际 {len(files)}"
    caps = [load_capsule(p) for p in files]
    ids = {c.chain.self_sha256 for c in caps}
    assert first["capsule_id"].split(":")[-1] in ids, "返回的 id 必须是盘上的文件名"
    assert second["capsule_id"].split(":")[-1] in ids

    genesis = [c for c in caps if c.chain.prev_sha256 == ZERO_SHA]
    assert len(genesis) == 1, "一轮只许一个 genesis"
    tail = next(c for c in caps if c.chain.prev_sha256 != ZERO_SHA)
    assert tail.chain.prev_sha256 in ids, "链要接得上"


def test_seal_failure_does_not_fake_an_id(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    """证据造不出来 → 明说哪一步没成，仍然不给假 id。"""
    from veripen_cli import cmd_hunt

    def boom(**kw):  # noqa: ANN003, ANN202
        raise RuntimeError("judgement missing route")

    monkeypatch.setattr(cmd_hunt, "build_capsule", boom)
    monkeypatch.setattr(cmd_hunt, "run_oracle", _verified)

    rt = cmd_hunt.HuntRuntime(_routeset(), _TransportStub(),
                              __import__("veripen_oracles").SymbolTable(),
                              capsules_dir=tmp_path / "capsules")
    out = rt.run_oracle("sqli_bool_diff", "", None)
    assert out["verdict"] == "VERIFIED"
    assert out["capsule_id"] is None
    assert out["evidence"].startswith("build_failed:")
    assert "证据构造失败" in out["evidence_note"]
    assert not list((tmp_path / "capsules").glob("*.json")), "失败就不该留下半份证据"


def test_hunt_refuses_to_reuse_an_evidence_dir(tmp_path, capsys) -> None:  # noqa: ANN001
    """hunt 现在也会落证据 → 同一个 --out 跑第二次必须被拒（与 run 同一条规矩）。"""
    from veripen_cli.cmd_hunt import main

    out = tmp_path / "hunt"
    (out / "capsules").mkdir(parents=True)
    (out / "capsules" / ("f" * 64 + ".json")).write_text("{}", encoding="utf-8")
    spec = tmp_path / "spec.json"
    spec.write_text('{"openapi":"3.0.0","paths":{}}', encoding="utf-8")

    rc = main(["--target", "http" + ":" + chr(0x2F) * 2 + LOOPBACK,
               "--openapi", str(spec), "--out", str(out)])
    assert rc == 2, "已有证据的目录必须拒跑"
    err = capsys.readouterr().err
    assert "拒绝重跑" in err and "--out" in err, err


@pytest.mark.parametrize("script", ["scripts/demo.py", "scripts/b26/agent_live.py"])
def test_demo_runtimes_do_not_fabricate(script: str) -> None:
    """演示脚本也走同一条工具通道——同样不许编。

    源码级检查在这里是**故意的**：这两个 runtime 的 `run_oracle` 要真靶场才跑得动，
    为一个字段值搭一整套夹具不划算；而"假 sha256"的形态极其固定，一眼可辨。
    """
    text = (ROOT / script).read_text(encoding="utf-8")
    assert 'f"sha256:{oracle_id}"' not in text, f"{script} 仍在编证据指针"
    assert '"evidence": "not_sealed"' in text or '"evidence": "pending_seal"' in text, script
