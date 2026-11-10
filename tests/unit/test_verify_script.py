"""B200：闸门清单只有一处——`scripts/verify.py`。

判据：`mypy` + 六道 lint + 账本 + 计划结构一直是**谁记得谁跑**。人手跑意味着两件事
同时发生：漏跑，以及"CI 跑的和本地跑的不是同一串命令"。

这组用例钉四件事（都不依赖外部程序）：
1. 清单可列举，且包含每道闸门；
2. 跳过 ≠ 通过：缺条件时退出码是 2，不是 0；
3. 有失败时退出码是 1，且失败项被点名；
4. `--only` 会拒绝不存在的闸门名（不静默跑空）。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for extra in (ROOT, ROOT / "scripts"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))


EXPECTED = {
    "mypy", "lint-noroute", "lint-nodata", "lint-ui",
    "lint-netimports", "lint-noleak", "spec-drift", "ledger", "plan",
}


def test_list_names_every_gate(capsys) -> None:  # noqa: ANN001
    import verify

    assert verify.main(["--list"]) == 0
    out = capsys.readouterr().out
    for name in EXPECTED:
        assert name in out, f"清单里少了 {name}"


def test_skip_is_not_pass() -> None:
    """缺条件 → 退出码 2（未全绿），不许记 0。"""
    import verify

    called: list[list[str]] = []

    def runner(argv):  # noqa: ANN001, ANN202
        called.append(list(argv))
        return 0, ""

    original = verify.available
    verify.available = lambda gate: gate.name != "plan"       # 假装没装 node
    try:
        code = verify.main([], runner=runner)
    finally:
        verify.available = original
    assert code == 2, f"有跳过却返回 {code}"
    assert called, "其余闸门应当照跑"


def test_failure_is_one_and_named(capsys) -> None:  # noqa: ANN001
    import verify

    def runner(argv):  # noqa: ANN001, ANN202
        return (1, "boom") if "ledger_check.py" in argv[-1] else (0, "")

    assert verify.main([], runner=runner) == 1
    out = capsys.readouterr().out
    assert "fail · ledger" in out


def test_unknown_gate_name_is_rejected() -> None:
    import pytest

    import verify

    with pytest.raises(SystemExit) as exc:
        verify.main(["--only", "no-such-gate"])
    assert "未知闸门" in str(exc.value)


def test_all_green_returns_zero() -> None:
    import verify

    assert verify.main([], runner=lambda argv: (0, "")) == 0


# ── B205：bench 冒烟在清单里，并说清"子集不是验收数字" ──────────
def test_bench_smoke_is_in_the_list_and_labelled() -> None:
    import verify

    smoke = [g for g in verify.SMOKES if g.name == "bench-smoke"]
    assert smoke, "冒烟闸门没进清单"
    gate = smoke[0]
    assert gate.kind == "smoke", gate.kind
    assert "--limit" in gate.argv, "子集跑必须显式限制条数"
    assert "--rounds" in gate.argv


def test_smoke_gates_run_without_with_tests(capsys) -> None:  # noqa: ANN001
    """冒烟属于快闸门：不带 --with-tests 也要跑到。"""
    import verify

    seen: list[str] = []

    def runner(argv):  # noqa: ANN001, ANN202
        seen.append(argv[-1])
        return 0, ""

    code = verify.main([], runner=runner)
    out = capsys.readouterr().out
    assert code == 0
    assert "bench-smoke" in out, "冒烟闸门没跑"
