"""B569 · 插件锁快照：**能认出手改**，且无插件时不得假装通过。

`check_lock_snapshot` 的 docstring 记着一段历史：

> 原来的写法是"sha 有 64 位就算 OK"——可 `Registry()` 是空的，那份 sha 就是
> **空对象的哈希**（`44136fa355b3…`），于是这条永远绿、什么也没验（B175）。

现在分三种情形。本批逐条钉住，并且**先证明那条"能红"判据抓得到手改**：

| 情形 | 必须 |
|---|---|
| 没有插件 | **skip**，并说明 0 条（不是"通过"，也不是"失败"） |
| 有插件、sha 与重算一致 | OK |
| 有插件、sha 对不上（锁被手改） | **FAIL** |

第三种是本轮的判据重点：一个"永远 OK"的实现也能过前两种。
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _check(registry: Any) -> Any:
    """把一个 registry 塞进 doctor 的检查里跑。

    用 `setattr` 而不是 `pkg.Registry = ...`：直接给**类型**赋值在 mypy --strict 下
    报 `Cannot assign to a type`，而 `setattr` + `delattr` 是运行期替换，
    类型系统不必参与——这里要的正是"临时换一个构造器"。
    """
    from hos_ares_cli import cmd_doctor

    import hos_ares_core.plugins as plugins_pkg

    original = plugins_pkg.Registry
    setattr(plugins_pkg, "Registry", lambda: registry)  # noqa: B010
    try:
        return cmd_doctor.check_lock_snapshot(ROOT)
    finally:
        setattr(plugins_pkg, "Registry", original)  # noqa: B010


# ── 1：无插件 → skip（不许假绿）──────────────────────────────
def test_empty_registry_is_skip_not_ok() -> None:
    """**空 registry 的 sha 是"空对象的哈希"** —— 拿它当 OK 等于什么都没验。

    这条正是 docstring 里那段历史的回归测试。
    """
    from hos_ares_core.plugins import Registry

    result = _check(Registry())
    assert result.status.name == "SKIP", result.status
    assert "0 条" in result.detail, result.detail
    assert result.fix, "skip 项也必须给下一步"


# ── 2：有插件且一致 → OK ────────────────────────────────────
def test_consistent_snapshot_is_ok() -> None:
    from hos_ares_core.plugins import Registry

    reg = Registry()
    reg._lock["p"] = type("P", (), {"as_dict": lambda self: {"version": "1.0.0"}})()  # noqa: SLF001
    result = _check(reg)
    assert result.status.name == "OK", result.detail
    assert "1 条" in result.detail, result.detail


# ── 3：锁被手改 → FAIL（本轮重点）──────────────────────────
def test_tampered_snapshot_is_fail() -> None:
    """**先证明这条判据抓得到**：把 `snapshot_sha256` 改掉，必须 FAIL。

    没有这条，"sha 长度是 64"那种假绿写法也能通过前两条。
    """
    from hos_ares_core.plugins import Registry

    class _Tampered(Registry):
        def snapshot_lock(self) -> dict[str, Any]:
            snap = super().snapshot_lock()
            snap["snapshot_sha256"] = "0" * 64      # 手改：长度对、值不对
            return snap

    reg = _Tampered()
    reg._lock["p"] = type("P", (), {"as_dict": lambda self: {"version": "1.0.0"}})()  # noqa: SLF001
    result = _check(reg)
    assert result.status.name == "FAIL", result.detail
    assert "快照与重算不一致" in result.fix, result.fix


def test_wrong_length_sha_is_also_fail() -> None:
    """长度不对也要 FAIL——但**长度对**才是关键那条（上一轮用例覆盖的）。"""
    from hos_ares_core.plugins import Registry

    class _Short(Registry):
        def snapshot_lock(self) -> dict[str, Any]:
            snap = super().snapshot_lock()
            snap["snapshot_sha256"] = "abc"
            return snap

    reg = _Short()
    reg._lock["p"] = type("P", (), {"as_dict": lambda self: {"version": "1.0.0"}})()  # noqa: SLF001
    assert _check(reg).status.name == "FAIL"


# ── 4：三种情形互斥（不许有第四种含糊状态）──────────────────
@pytest.mark.parametrize("case", ["empty", "consistent", "tampered"])
def test_every_case_lands_in_exactly_one_status(case: str) -> None:
    """每个情形都必须落到**确定的一种**状态，不许"既像 skip 又像 ok"。"""
    from hos_ares_core.plugins import Registry

    if case == "empty":
        status = _check(Registry()).status.name
        assert status == "SKIP"
        return

    reg = Registry()
    reg._lock["p"] = type("P", (), {"as_dict": lambda self: {"version": "1.0.0"}})()  # noqa: SLF001
    if case == "tampered":
        reg.snapshot_lock = lambda: {  # type: ignore[method-assign]
            **Registry.snapshot_lock(reg), "snapshot_sha256": "0" * 64,
        }
    expected = "OK" if case == "consistent" else "FAIL"
    assert _check(reg).status.name == expected
