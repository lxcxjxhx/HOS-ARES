"""B5 OOB 通道：符号注入 / 回调归因 / 本地回环监听（零公网）。

规矩（07 §3）：**OOB 接入点是配置符号，不是常量**；拿不到符号就 INCONCLUSIVE，不猜。
"""

from __future__ import annotations

import pytest
from conftest import Lab

from veripen_oracles.common.harness import SymbolMissing, SymbolTable, run
from veripen_oracles.oob import LabOob, OobProbe
from veripen_oracles import tierA
from veripen_core.models import Verdict


# ── 符号表 ──────────────────────────────────────────────────────────
def test_symbol_missing_is_hard_failure() -> None:
    table = SymbolTable({})
    with pytest.raises(SymbolMissing):
        table.resolve("{{ref:OOB}}/x")
    with pytest.raises(SymbolMissing):
        table.resolved("OOB")


def test_symbol_substitution() -> None:
    table = SymbolTable({"OOB": "oob-SAMPLE"})
    assert table.resolve("{{ref:OOB}}/{{nonce}}") == "oob-SAMPLE/{{nonce}}"


def test_from_env_names_skips_absent() -> None:
    table = SymbolTable.from_env_names({"OOB": "VP_OOB"}, {"VP_OOB": "x"})
    assert table.resolved("OOB") == "x"
    empty = SymbolTable.from_env_names({"OOB": "VP_OOB"}, {})
    with pytest.raises(SymbolMissing):
        empty.resolved("OOB")


# ── 回调归因 ────────────────────────────────────────────────────────
def test_probe_requires_nonce_match(vulnerable: Lab) -> None:
    probe = OobProbe(LabOob(app=vulnerable.app, endpoint_value="oob-SAMPLE"))
    mark = probe.mark()
    vulnerable.app.callbacks.append({"url": "oob-SAMPLE/AAA", "ua": "x"})
    assert probe.check(mark, "AAA").hit is True
    assert probe.check(mark, "BBB").hit is False


def test_callback_from_self_is_not_hit(vulnerable: Lab) -> None:
    probe = OobProbe(LabOob(app=vulnerable.app, endpoint_value="oob-SAMPLE"))
    mark = probe.mark()
    vulnerable.app.callbacks.append({"url": "oob-SAMPLE/AAA", "ua": "x"})
    verdict = probe.check(mark, "AAA", self_hosts=("lab-app",))
    assert verdict.hit is False
    assert verdict.reason == "callback_from_self"


def test_old_callbacks_do_not_count(vulnerable: Lab) -> None:
    probe = OobProbe(LabOob(app=vulnerable.app, endpoint_value="oob-SAMPLE"))
    vulnerable.app.callbacks.append({"url": "oob-SAMPLE/OLD", "ua": "x"})
    mark = probe.mark()  # 在此之前就有的回调不算
    assert probe.check(mark, "OLD").hit is False


# ── SSRF oracle 依赖 OOB：缺席即不算成立 ────────────────────────────
def test_ssrf_without_symbol_is_not_verified(vulnerable: Lab) -> None:
    with pytest.raises(SymbolMissing):
        run(tierA.by_id("ssrf_oob"), vulnerable.binding("url_fetch"), vulnerable.transport,
            symbols=SymbolTable({}), nonce="n")


def test_ssrf_counts_only_new_callback(vulnerable: Lab) -> None:
    """靶场里预先已有同 nonce 的回调（理论上不该发生）不应让判定成立。"""
    oracle = tierA.by_id("ssrf_oob")
    result = run(oracle, vulnerable.binding("url_fetch"), vulnerable.transport,
                 symbols=vulnerable.symbols, nonce="fresh-nonce")
    assert result.verdict is Verdict.VERIFIED
    assert result.judgement is not None
    assert result.judgement.assertion["observed"]["oob_seen"] is True
    assert result.judgement.assertion["observed"]["control_seen"] is False
