"""B175：doctor 的「插件锁快照」原来永远绿。

判据：`check_lock_snapshot` 只是判断 `snapshot_sha256` 有没有 64 位，
而 `Registry()` 是空的——那份 sha 就是**空对象 `{}` 的哈希**（`44136fa355b3…`）。
换言之：这一项验的是"空字典能算出哈希"，跟"锁完好"没有关系。

现在的口径：
* 没有插件 → **skip**（0 条，没有可验的东西），不是绿；
* 有插件且 sha 与按 pin 重算一致 → OK；
* 有插件但重算对不上 → **fail**（快照坏了，可复现就不成立）。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for extra in (ROOT, ROOT / "tests"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

PINS = {"p-one": {"resolved": "1.0.0", "sha256": "a" * 64}}


class _FakeRegistry:
    def __init__(self, snap: dict) -> None:
        self._snap = snap

    def snapshot_lock(self) -> dict:
        return self._snap


def _snap(sha: str) -> dict:
    return {"version": 1, "lock": PINS, "snapshot_sha256": sha}


def _run(monkeypatch, snap: dict):  # noqa: ANN001, ANN202
    import veripen_core.plugins as plugins
    from veripen_cli.cmd_doctor import check_lock_snapshot

    monkeypatch.setattr(plugins, "Registry", lambda: _FakeRegistry(snap))
    return check_lock_snapshot(ROOT)


def test_empty_registry_is_a_skip_not_a_green(monkeypatch) -> None:  # noqa: ANN001
    from veripen_cli.cmd_doctor import Status

    result = _run(monkeypatch, {"version": 1, "lock": {}, "snapshot_sha256": "0" * 64})
    assert result.status is Status.SKIP, "空锁只能是 skip：它没有可验的东西"
    assert "0 条" in result.detail


def test_consistent_snapshot_is_ok(monkeypatch) -> None:  # noqa: ANN001
    from veripen_core.plugins.registry import lock_sha
    from veripen_cli.cmd_doctor import Status

    result = _run(monkeypatch, _snap(lock_sha(PINS)))
    assert result.status is Status.OK
    assert "1 条" in result.detail


def test_tampered_snapshot_fails(monkeypatch) -> None:  # noqa: ANN001
    """锁文件被手改 → 必须红，并说清该改哪里。"""
    from veripen_cli.cmd_doctor import Status

    result = _run(monkeypatch, _snap("b" * 64))
    assert result.status is Status.FAIL
    assert result.fix, "失败项必须带 fix"


def test_lock_sha_is_independent_of_dict_order() -> None:
    from veripen_core.plugins.registry import lock_sha

    a = {"x": {"resolved": "1", "sha256": "a" * 64}, "y": {"resolved": "2", "sha256": "b" * 64}}
    b = {"y": a["y"], "x": a["x"]}
    assert lock_sha(a) == lock_sha(b)
