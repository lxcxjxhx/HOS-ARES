"""B187：档里配了 `scope.deny`，闸门的 deny 永远是空集。

判据：

```
profiles/demo-a/profile.yaml  →  scope: { deny: [DENY_SET_DEFAULT] }
load_profile(...)             →  Profile.deny 有值（还进了档哈希）
build_guard(...)              →  deny=frozenset()      ← 两处都写死空集
```

于是"配了黑名单"只是配了；真正决定发不发包的那道闸门从来没收到它。

本轮：`build_guard(deny=…)`、`run --deny`、`hunt --deny`；
`platform` 把档的 deny 里的资产映射成主机传下去（不是静默忽略，也不是整批拒绝——
被禁的那个资产整面 BLOCKED，其余照跑），未知条目直接拒跑（fail-closed）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
for extra in (ROOT, ROOT / "tests"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

SEP = chr(0x2F)
DEAD = "http" + ":" + SEP + SEP + "127." + "0.0.1:9"


def _spec(tmp_path: Path) -> Path:
    path = tmp_path / "spec.json"
    path.write_text(json.dumps({
        "openapi": "3.0.0", "info": {"title": "x", "version": "1"},
        "servers": [{"url": DEAD}],
        "paths": {SEP + "api" + SEP + "x": {"get": {"x-veripen-tags": ["param_injectable"]}}},
    }), encoding="utf-8")
    return path


# ── 闸门真的收到 deny ─────────────────────────────────────────
def test_run_passes_deny_into_the_guard(monkeypatch: pytest.MonkeyPatch,
                                        tmp_path: Path) -> None:
    import veripen_cli.cmd_run as cmd_run

    seen: dict[str, Any] = {}

    def record_guard(host: str, routeset: Any, oracles: Any, *, rate: float = 1.0,
                     burst: float = 2.0, deny: Any = (),
                     ceiling: str = "read_only") -> Any:  # noqa: ANN401
        seen["deny"] = list(deny)
        raise SystemExit(0)

    monkeypatch.setattr(cmd_run, "build_guard", record_guard)
    with pytest.raises(SystemExit):
        cmd_run.main(["--target", DEAD, "--openapi", str(_spec(tmp_path)),
                      "--deny", "a.invalid,b.invalid", "--out", str(tmp_path / "o")])
    assert seen["deny"] == ["a.invalid", "b.invalid"]


def test_run_without_deny_stays_empty(monkeypatch: pytest.MonkeyPatch,
                                      tmp_path: Path) -> None:
    import veripen_cli.cmd_run as cmd_run

    seen: dict[str, Any] = {}

    def record_guard(host: str, routeset: Any, oracles: Any, *, rate: float = 1.0,
                     burst: float = 2.0, deny: Any = (),
                     ceiling: str = "read_only") -> Any:  # noqa: ANN401
        seen["deny"] = list(deny)
        raise SystemExit(0)

    monkeypatch.setattr(cmd_run, "build_guard", record_guard)
    with pytest.raises(SystemExit):
        cmd_run.main(["--target", DEAD, "--openapi", str(_spec(tmp_path)),
                      "--out", str(tmp_path / "o")])
    assert seen["deny"] == [], "没人给 --deny 时不许有意外黑名单"


# ── 档的 deny 传下去 ──────────────────────────────────────────
@pytest.fixture()
def profile_with_deny(tmp_path: Path) -> tuple[Path, str]:
    raw = (ROOT / "profiles" / "demo-a" / "profile.yaml").read_text(encoding="utf-8")
    raw = raw.replace("deny: [DENY_SET_DEFAULT]", "deny: [web-01]")
    path = tmp_path / "pf" / "profile.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(raw, encoding="utf-8")
    return path, "web-01"


def _platform(tmp_path: Path, profile: Path, spec: Path, *extra: str) -> int:
    from veripen_cli.cmd_platform import main

    return main(["--profile", str(profile), "--openapi", str(spec),
                 "--out", str(tmp_path / "out"), "--db", str(tmp_path / "out" / "p.db"),
                 *extra])


def test_platform_forwards_profile_deny(profile_with_deny: tuple[Path, str],
                                        tmp_path: Path,
                                        monkeypatch: pytest.MonkeyPatch) -> None:
    from veripen_cli import cmd_run

    profile, asset_id = profile_with_deny
    monkeypatch.setenv("VP_ASSET_WEB_01", DEAD)
    seen: list[list[str]] = []
    monkeypatch.setattr(cmd_run, "main", lambda argv: seen.append(list(argv)) or 0)

    rc = _platform(tmp_path, profile, _spec(tmp_path), "--approve")
    assert rc == 0
    argv = seen[0]
    assert "--deny" in argv, f"档的 deny 没传下去：{argv}"
    denied = argv[argv.index("--deny") + 1]
    # 档里写的是资产 id，闸门认的是主机——映射要发生
    assert any(part.startswith("127.") for part in denied.split(",")), denied
    assert asset_id not in denied.split(","), "把资产 id 当主机传下去等于没设"


def test_platform_passes_host_style_deny_through(tmp_path: Path,
                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    """deny 里也可以直接写主机名：原样传下去，别当未知资产拒跑。"""
    from veripen_cli import cmd_run

    raw = (ROOT / "profiles" / "demo-a" / "profile.yaml").read_text(encoding="utf-8")
    raw = raw.replace("deny: [DENY_SET_DEFAULT]", "deny: [other.invalid]")
    profile = tmp_path / "pf" / "profile.yaml"
    profile.parent.mkdir(parents=True, exist_ok=True)
    profile.write_text(raw, encoding="utf-8")
    monkeypatch.setenv("VP_ASSET_WEB_01", DEAD)
    seen: list[list[str]] = []
    monkeypatch.setattr(cmd_run, "main", lambda argv: seen.append(list(argv)) or 0)

    assert _platform(tmp_path, profile, _spec(tmp_path), "--approve") == 0
    argv = seen[0]
    assert "other.invalid" in argv[argv.index("--deny") + 1]


# ── 闸门的 plan 之前根本没被调用过（B187 的真正缺陷）──────────
def test_guard_plan_actually_blocks_a_denied_host() -> None:
    from veripen_core.guard.scope import RouteSet
    from veripen_cli.cmd_hunt import build_guard

    routeset = RouteSet(bindings=(), asset_id="probe", resolver_version="1",
                        query_hash="0" * 64, created_at=0, expires_at=0,
                        sources_used=(), sources_failed=(), unresolved=(),
                        routeset_sha256="0" * 64)
    guard = build_guard("deny.invalid", routeset, ("sqli_bool_diff",),
                        deny=["deny.invalid"])
    assert not guard.plan.is_allowed("sqli_bool_diff"), "黑名单主机必须整面挡掉"
    assert guard.plan.blocked["sqli_bool_diff"] == "asset_not_in_scope"


def test_guard_plan_enforces_the_impact_ceiling() -> None:
    from veripen_core.guard.scope import RouteSet
    from veripen_cli.cmd_hunt import build_guard

    routeset = RouteSet(bindings=(), asset_id="probe", resolver_version="1",
                        query_hash="0" * 64, created_at=0, expires_at=0,
                        sources_used=(), sources_failed=(), unresolved=(),
                        routeset_sha256="0" * 64)
    low = build_guard("ok.invalid", routeset, ("mass_assignment",))
    assert not low.plan.is_allowed("mass_assignment"), "写类默认不许跑"
    assert low.plan.blocked["mass_assignment"] == "impact_exceeds_ceiling"

    high = build_guard("ok.invalid", routeset, ("mass_assignment",),
                       ceiling="reversible_write")
    assert high.plan.is_allowed("mass_assignment"), "显式抬高顶棚后才许跑"
