"""B189：报告里的复现命令是操作员机器上的**绝对路径**。

判据（一次真实 run 的报告）：

```
- 复现（离线重判）：`veripen replay --capsule C:/Users/<操作员>/AppData/Local/Temp/.../capsules/<sha>.json`
```

报告是要发出去的东西。绝对路径把操作员的目录结构写进交付物，换台机器就失效；
而 `findings.json` 里**根本没有**证据路径字段——机器消费者只能自己猜
`<out>/capsules/<self_sha256>.json` 这个命名约定。

现在的口径：报告与 findings.json 都用**相对 `--out`** 的路径，并写明"在报告所在目录执行"。
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
for extra in (ROOT, ROOT / "tests", ROOT / "tests" / "unit"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

SEP = chr(0x2F)
CMD = re.compile(r"`(veripen replay --capsule ([^`\s]+)[^`]*)`")


def _spec(base: str) -> str:
    return json.dumps({
        "openapi": "3.0.0", "info": {"title": "lab", "version": "1"},
        "servers": [{"url": base}],
        "paths": {f"{SEP}api{SEP}search": {"get": {"x-veripen-tags": ["param_injectable"]}}},
    })


@pytest.fixture()
def run_dir(tmp_path: Path) -> Path:
    from lab.app import App
    from lab.net_server import LabServer
    from veripen_cli.cmd_run import main

    srv = LabServer(App(secure=False)).start()
    spec = tmp_path / "spec.json"
    spec.write_text(_spec(srv.base_url), encoding="utf-8")
    out = tmp_path / "run"
    try:
        assert main(["--target", srv.base_url, "--openapi", str(spec),
                     "--out", str(out)]) == 0
    finally:
        srv.stop()
    return out


def test_report_paths_are_relative(run_dir: Path) -> None:
    md = (run_dir / "report.md").read_text(encoding="utf-8")
    matched: list[tuple[str, str]] = CMD.findall(md)
    assert matched, "报告里没有复现命令"
    for _cmd, path in matched:
        assert not Path(path).is_absolute(), f"报告里出现绝对路径：{path}"
        assert (run_dir / path).is_file(), f"相对路径解析不到：{path}"


def test_json_carries_the_capsule_path(run_dir: Path) -> None:
    doc: dict[str, Any] = json.loads((run_dir / "findings.json").read_text(encoding="utf-8"))
    reported = [f for f in doc["findings"] if f.get("outcome") == "reported"]
    assert reported, "这一轮没有可报结论，用例前提不成立"
    for f in reported:
        rel = (f.get("evidence") or {}).get("capsule_path")
        assert rel, f"finding {f.get('id')} 没有证据路径"
        assert not Path(rel).is_absolute()
        assert (run_dir / rel).is_file()


def test_the_command_runs_from_the_report_directory(run_dir: Path,
                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    """报告里怎么写的，就怎么跑——换个工作目录必须还能跑通。"""
    from veripen_cli.cmd_replay import main

    md = (run_dir / "report.md").read_text(encoding="utf-8")
    _cmd, rel = CMD.findall(md)[0]
    monkeypatch.chdir(run_dir)
    assert main(["--capsule", rel, "--mode", "offline"]) == 0


def test_markdown_says_where_to_run_it(run_dir: Path) -> None:
    md = (run_dir / "report.md").read_text(encoding="utf-8")
    assert "在报告所在目录执行" in md, "相对路径必须配一句在哪执行"
