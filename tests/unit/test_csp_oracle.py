

# ── B381：expected 必须随结论方向变 ─────────────────────────────
def test_expected_flips_with_the_verdict_direction() -> None:
    """`expected.has_protection` 恒为 [1] 时，"头缺失"那份证据与自己的结论相反。

    这三个 oracle 从 B123 起没有 bench 用例，所以这处一直没被重放过；
    补用例那天才发现（B381 记录）。
    """
    from types import SimpleNamespace

    from hos_ares_oracles.common.harness import Pair
    from hos_ares_oracles.tier_headers import CspAbsent

    def _pair(role: str, headers: dict[str, str]) -> Pair:
        trace = SimpleNamespace(status=200, headers=headers, error=None, body=b"{}",
                                is_normal=lambda: True)
        return Pair(variant=SimpleNamespace(role=role), baseline=trace, trace=trace)

    absent = CspAbsent().judge([_pair("probe", {}), _pair("control", {})])
    assert absent.verdict.value == "VERIFIED"
    assert absent.assertion["expected"]["has_protection"] == [0]

    present = CspAbsent().judge([
        _pair("probe", {"Content-Security-Policy": "default-src 'self'"}),
        _pair("control", {"Content-Security-Policy": "default-src 'self'"}),
    ])
    assert present.verdict.value == "REFUTED"
    assert present.assertion["expected"]["has_protection"] == [1]
