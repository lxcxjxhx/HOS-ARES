"""B405 · 全量 oracle「活着」巡检：注册过的每一条都必须能跑出一个**可解释的结果**。

判据：把每条 oracle 对着靶场里它标签对应的面跑一次，结果必须是
「有判定」或「明确说缺什么」（`SymbolMissing` / REQUIRES 不满足）。
**任何其他异常都是缺陷**——B404 的教训是 `judge_burst` 写了却没人调，
而这类"静默失效"不会以异常形式出现；能被异常抓到的，就必须让异常把它抓住。

这张表同时是**能力清单**：哪条 oracle 需要第二张面、哪条需要符号，
在这里一眼能看出来（以前只散落在各处的注释里）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
for extra in (ROOT, ROOT / "tests", ROOT / "scripts" / "b12"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))


@pytest.fixture(scope="module")
def lab():  # noqa: ANN201
    from lab.app import App
    from lab.net_server import LabServer

    srv = LabServer(App(secure=False)).start()
    try:
        yield srv
    finally:
        srv.stop()


def test_every_registered_oracle_produces_an_explainable_result(lab) -> None:  # noqa: ANN001
    from lab.app import make_token
    from lab.net_transport import NetTransport

    import run_bench as RB
    from hos_ares_oracles import ALL_ORACLES
    from hos_ares_oracles.common import harness as H

    rs = RB._routeset(lab.base_url, ())
    by_tag: dict[str, object] = {}
    for binding in rs.bindings:
        for tag in binding.semantic_tags:
            by_tag.setdefault(tag, binding)
    transport = NetTransport(token_for=make_token)

    unexpected: list[str] = []
    no_face: list[str] = []
    ran = 0
    for cls in ALL_ORACLES:
        oracle = cls()
        tag = (oracle.meta.selector_tags or ("",))[0]
        binding = by_tag.get(tag)
        if binding is None:
            no_face.append(oracle.meta.id)
            continue
        try:
            H.run(oracle, binding, transport)  # type: ignore[arg-type]
            ran += 1
        except H.SymbolMissing:
            # 缺第二张面/缺符号：明确说缺什么，不是缺陷
            ran += 1
        except Exception as exc:  # noqa: BLE001 - 这里就是要把"没预料到的"全抓住
            unexpected.append(f"{oracle.meta.id}: {type(exc).__name__}: {exc}")

    assert not unexpected, "这些 oracle 抛了非预期异常（静默失效或实现错误）：\n  " + "\n  ".join(
        unexpected)
    # 巡检必须真的覆盖到大部分注册项：全被 NO_FACE 跳过等于什么都没测
    assert ran >= 20, f"只跑到 {ran} 条，检查靶场标签是否漏了；NO_FACE={no_face}"
