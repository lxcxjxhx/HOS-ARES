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


def _routeset():  # noqa: ANN202
    """一条 crawl 面即可：本用例不打网络，`run_oracle` 会被替身接管。"""
    import time

    from veripen_core.routes import (
        AssetRef, Resolver, RouteCandidate, RouteHints, RouteProvenance, RouteQuery,
    )
    from veripen_core.routes.sources import StaticSource

    cand = RouteCandidate(
        method="GET",
        template=SEP + "a",
        param_names=("x",),
        source=RouteProvenance.CRAWL,
        response_shape={"semantic_tags": ["param_injectable"]},
    )
    return Resolver(sources=[StaticSource("crawl", RouteProvenance.CRAWL, [cand])]).resolve(
        RouteQuery(asset=AssetRef(id=LOOPBACK), hints=RouteHints(), min_confidence=0.0),
        now=int(time.time()),
    )


def test_hunt_tool_result_has_no_fake_capsule(monkeypatch) -> None:
    """hunt 不封 capsule → 结果里必须说 `not_sealed`，而不是编一个 sha256 号。"""
    from veripen_cli import cmd_hunt
    from veripen_core.models import Verdict
    from veripen_oracles.common.harness import RunResult

    def fake_run_oracle(oracle, binding, transport, **kw):  # noqa: ANN001, ANN003, ANN202
        return RunResult(Verdict.VERIFIED, "verified_for_test", None, (), {})

    monkeypatch.setattr(cmd_hunt, "run_oracle", fake_run_oracle)

    class _TransportStub:
        oracle_id = ""

    rt = cmd_hunt.HuntRuntime(_routeset(), transport=_TransportStub(),
                              symbols=__import__("veripen_oracles").SymbolTable())
    out = rt.run_oracle("sqli_bool_diff", "", None)

    assert out["verdict"] == "VERIFIED"
    assert out["capsule_id"] is None, f"编了证据指针：{out.get('capsule_id')!r}"
    assert out["evidence"] == "not_sealed"
    assert "没有可重放证据" in out["evidence_note"]


@pytest.mark.parametrize("script", ["scripts/demo.py", "scripts/b26/agent_live.py"])
def test_demo_runtimes_do_not_fabricate(script: str) -> None:
    """演示脚本也走同一条工具通道——同样不许编。

    源码级检查在这里是**故意的**：这两个 runtime 的 `run_oracle` 要真靶场才跑得动，
    为一个字段值搭一整套夹具不划算；而"假 sha256"的形态极其固定，一眼可辨。
    """
    text = (ROOT / script).read_text(encoding="utf-8")
    assert 'f"sha256:{oracle_id}"' not in text, f"{script} 仍在编证据指针"
    assert '"evidence": "not_sealed"' in text or '"evidence": "pending_seal"' in text, script
