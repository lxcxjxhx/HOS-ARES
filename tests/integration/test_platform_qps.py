"""B186：档里写着 `policy.qps: 20`，实际一直是 1 qps。

判据：

```
profiles/demo-a/profile.yaml  →  policy: { qps: 20, concurrency: 2 }
build_guard(...)              →  rate=1.0, burst=2.0      ← 写死的默认值
cmd_run / cmd_platform        →  都不读 profile.qps
```

于是“按档跑”这句话里，唯一没落实的就是**限速**：操作员写 20，引擎跑 1，
而报告里也没说清到底按多少在跑。

本轮：
* `run` 新增 `--qps`（默认 1.0，与闸门默认一致，行为不变）；
* `platform` 把档里的 `policy.qps` 传下去，命令行 `--qps` 可覆盖；
* 报告里如实写：来自档 / 命令行覆盖 / 未指定，以及并发值“未被使用”。
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


# ── run：--qps 真的进闸门 ─────────────────────────────────────
def test_run_passes_qps_into_the_guard(monkeypatch: pytest.MonkeyPatch,
                                       tmp_path: Path) -> None:
    import veripen_cli.cmd_run as cmd_run

    seen: dict[str, Any] = {}

    def record_guard(host: str, routeset: Any, oracles: Any, *, rate: float = 1.0,
                     burst: float = 2.0) -> Any:  # noqa: ANN401
        seen["rate"] = rate
        seen["burst"] = burst
        raise SystemExit(0)          # 到闸门就够了，后面的出网不跑

    monkeypatch.setattr(cmd_run, "build_guard", record_guard)
    with pytest.raises(SystemExit):
        cmd_run.main(["--target", DEAD, "--openapi", str(_spec(tmp_path)),
                      "--qps", "7.5", "--out", str(tmp_path / "o")])
    assert seen["rate"] == 7.5 and seen["burst"] == 7.5


def test_run_default_qps_is_unchanged(monkeypatch: pytest.MonkeyPatch,
                                      tmp_path: Path) -> None:
    import veripen_cli.cmd_run as cmd_run

    seen: dict[str, Any] = {}

    def record_guard(host: str, routeset: Any, oracles: Any, *, rate: float = 1.0,
                     burst: float = 2.0) -> Any:  # noqa: ANN401
        seen["rate"] = rate
        seen["burst"] = burst
        raise SystemExit(0)

    monkeypatch.setattr(cmd_run, "build_guard", record_guard)
    with pytest.raises(SystemExit):
        cmd_run.main(["--target", DEAD, "--openapi", str(_spec(tmp_path)),
                      "--out", str(tmp_path / "o")])
    assert seen["rate"] == 1.0, "默认不许变：没人给 --qps 时仍是 1 qps"
    assert seen["burst"] == 2.0, "默认 burst 也不许变"


# ── platform：档的 qps 传下去，命令行能覆盖 ────────────────────
@pytest.fixture()
def profile_yaml(tmp_path: Path) -> Path:
    raw = (ROOT / "profiles" / "demo-a" / "profile.yaml").read_text(encoding="utf-8")
    # 把档里的 qps/concurrency 改成好认的值（其余照旧）
    raw = raw.replace("qps: 20", "qps: 13").replace("concurrency: 2", "concurrency: 3")
    path = tmp_path / "pf" / "profile.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(raw, encoding="utf-8")
    return path


def _platform(tmp_path: Path, profile: Path, spec: Path, *extra: str) -> int:
    from veripen_cli.cmd_platform import main

    return main(["--profile", str(profile), "--openapi", str(spec),
                 "--out", str(tmp_path / "out"), "--db", str(tmp_path / "out" / "p.db"),
                 *extra])


def test_platform_passes_profile_qps(profile_yaml: Path, tmp_path: Path,
                                     monkeypatch: pytest.MonkeyPatch) -> None:
    from veripen_cli import cmd_platform, cmd_run

    monkeypatch.setenv("VP_ASSET_WEB_01", DEAD)
    seen: list[list[str]] = []
    monkeypatch.setattr(cmd_run, "main", lambda argv: seen.append(list(argv)) or 0)

    rc = _platform(tmp_path, profile_yaml, _spec(tmp_path), "--approve")
    assert rc == 0
    argv = seen[0]
    assert "--qps" in argv, f"档的 qps 没传下去：{argv}"
    assert argv[argv.index("--qps") + 1] == "13"


def test_platform_qps_flag_overrides_profile(profile_yaml: Path, tmp_path: Path,
                                             monkeypatch: pytest.MonkeyPatch) -> None:
    from veripen_cli import cmd_run

    monkeypatch.setenv("VP_ASSET_WEB_01", DEAD)
    seen: list[list[str]] = []
    monkeypatch.setattr(cmd_run, "main", lambda argv: seen.append(list(argv)) or 0)

    _platform(tmp_path, profile_yaml, _spec(tmp_path), "--approve", "--qps", "5")
    assert seen[0][seen[0].index("--qps") + 1] == "5"


def test_report_says_which_rate_was_used(profile_yaml: Path, tmp_path: Path,
                                         monkeypatch: pytest.MonkeyPatch) -> None:
    from veripen_cli import cmd_run

    monkeypatch.setenv("VP_ASSET_WEB_01", DEAD)
    monkeypatch.setattr(cmd_run, "main", lambda argv: 0)
    _platform(tmp_path, profile_yaml, _spec(tmp_path), "--approve")
    report = (tmp_path / "out" / "platform-report.md").read_text(encoding="utf-8")
    assert "限速：13 qps（档 policy.qps）" in report, report[:400]
    assert "并发：档要求 3" in report and "未使用" in report, "并发没落实就得说清"
