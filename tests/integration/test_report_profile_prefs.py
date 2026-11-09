"""B197：档里的报告偏好（框架 / 分发级别）一条也没往下传。

判据：

```
profiles/demo-a/profile.yaml   report: {frameworks: [owasp_wstg, cwe], distribution: internal}
load_profile(...)              → frameworks 有值；distribution **根本没解析**
cmd_run / cmd_platform         → 都不传
report.md                      → "## 5. 合规映射" 这一节永远不出现（它只在 frameworks 非空时渲染）
```

于是档里写的报告口径只影响档的哈希，不影响报告本身。

本轮：`--frameworks` / `--distribution` 进 `run`，`platform` 按档透传；
`report.distribution` 真正解析并校验；平台报告加一行"报告口径"，读者知道每个资产的报告按什么出。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
for extra in (ROOT, ROOT / "tests", ROOT / "tests" / "unit"):
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


@pytest.fixture()
def lab_run(tmp_path: Path):  # noqa: ANN201
    from lab.app import App
    from lab.net_server import LabServer

    srv = LabServer(App(secure=False)).start()
    spec = tmp_path / "lab.json"
    spec.write_text(json.dumps({
        "openapi": "3.0.0", "info": {"title": "lab", "version": "1"},
        "servers": [{"url": srv.base_url}],
        "paths": {SEP + "api" + SEP + "search": {"get": {"x-veripen-tags": ["param_injectable"]}}},
    }), encoding="utf-8")
    try:
        yield srv.base_url, spec
    finally:
        srv.stop()


def test_run_renders_the_compliance_section(lab_run, tmp_path: Path) -> None:  # noqa: ANN001
    from veripen_cli.cmd_run import main

    base, spec = lab_run
    out = tmp_path / "run"
    assert main(["--target", base, "--openapi", str(spec), "--out", str(out),
                 "--frameworks", "owasp_wstg,cwe", "--distribution", "customer"]) == 0

    md = (out / "report.md").read_text(encoding="utf-8")
    assert "## 5. 合规映射" in md, "给了框架却不渲染这一节"
    assert "- owasp_wstg" in md and "- cwe" in md
    assert "customer" in md, "分发级别要写进附录"

    doc = json.loads((out / "findings.json").read_text(encoding="utf-8"))
    assert doc["frameworks"] == ["owasp_wstg", "cwe"]
    assert doc["env"]["distribution"] == "customer"


def test_run_without_frameworks_has_no_compliance_section(lab_run, tmp_path: Path) -> None:  # noqa: ANN001
    from veripen_cli.cmd_run import main

    base, spec = lab_run
    out = tmp_path / "run"
    assert main(["--target", base, "--openapi", str(spec), "--out", str(out)]) == 0
    md = (out / "report.md").read_text(encoding="utf-8")
    assert "## 5. 合规映射" not in md, "没给框架就不要编一节出来"


def test_profile_distribution_is_parsed_and_validated(tmp_path: Path) -> None:
    from veripen_core.profile import load_profile, validate_profile

    raw = (ROOT / "profiles" / "demo-a" / "profile.yaml").read_text(encoding="utf-8")
    assert "distribution: internal" in raw

    path = tmp_path / "pf" / "profile.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(raw, encoding="utf-8")
    prof = load_profile(path)
    assert prof.distribution == "internal"

    bad = raw.replace("distribution: internal", "distribution: public")
    path.write_text(bad, encoding="utf-8")
    prof = load_profile(path)
    from veripen_oracles import ALL_ORACLES

    issues = validate_profile(
        prof,
        known_oracles={c.meta.id for c in ALL_ORACLES},
        known_tags={s.semantic_tag for s in prof.selectors},
    )
    assert any(i.code == "distribution_unknown" for i in issues), issues


def test_platform_forwards_profile_report_prefs(tmp_path: Path,
                                                monkeypatch: pytest.MonkeyPatch) -> None:
    from veripen_cli import cmd_run

    raw = (ROOT / "profiles" / "demo-a" / "profile.yaml").read_text(encoding="utf-8")
    raw = raw.replace("distribution: internal", "distribution: partner")
    profile = tmp_path / "pf" / "profile.yaml"
    profile.parent.mkdir(parents=True, exist_ok=True)
    profile.write_text(raw, encoding="utf-8")
    monkeypatch.setenv("VP_ASSET_WEB_01", DEAD)

    seen: list[list[str]] = []
    monkeypatch.setattr(cmd_run, "main", lambda argv: seen.append(list(argv)) or 0)
    from veripen_cli.cmd_platform import main

    assert main(["--profile", str(profile), "--openapi", str(_spec(tmp_path)),
                 "--out", str(tmp_path / "out"), "--db", str(tmp_path / "out" / "p.db"),
                 "--approve"]) == 0

    argv = seen[0]
    assert "--distribution" in argv and argv[argv.index("--distribution") + 1] == "partner"
    assert "--frameworks" in argv
    fw = argv[argv.index("--frameworks") + 1]
    assert "owasp_wstg" in fw and "cwe" in fw

    report = (tmp_path / "out" / "platform-report.md").read_text(encoding="utf-8")
    assert "报告口径：分发 partner" in report
