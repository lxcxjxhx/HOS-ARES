"""B165：把"hunt / platform 的证据能跑"补成同级契约。

B160 只钉了 `run` 与演示脚本的报告面。另外两个出口当时没有断言：

- `hunt` 没有 report，但它的 `hunt.json.evidence` 指向真实 capsule——
  这些文件必须存在、链必须完整、**离线重判不许翻案**；
- `platform` 的汇总报告引用子资产产物——每个 `asset-N/capsules` 要能独立验，
  报告里那一列 capsule 数要等于结论真正引用的份数。

判据只有一条：**读者拿到的每一个 id，都能变成一次可执行的重判**。
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

S = chr(0x2F)


def _lab():  # noqa: ANN202
    from lab.app import App
    from lab.net_server import LabServer

    return LabServer(App(secure=False)).start()


def _spec(tmp: Path, base: str) -> str:
    spec = tmp / "spec.json"
    spec.write_text(json.dumps({
        "openapi": "3.0.0", "info": {"title": "lab", "version": "1"},
        "servers": [{"url": base}],
        "paths": {
            S + "api" + S + "search": {"get": {"x-veripen-tags": ["param_injectable"]}},
            S + "api" + S + "read": {"get": {"x-veripen-tags": ["file_read"]}},
        },
    }), encoding="utf-8")
    return str(spec)


def _offline(cap_path: Path) -> str:
    from veripen_core.evidence import ReplayEnv, ReplayMode, load_capsule, replay

    return replay(load_capsule(cap_path), ReplayEnv(now=0, mode=ReplayMode.OFFLINE)).state


# ── hunt：证据文件在、链完整、离线重判不翻案 ────────────────────
def test_hunt_evidence_is_replayable(tmp_path: Path) -> None:
    from veripen_cli.cmd_hunt import main

    srv = _lab()
    out = tmp_path / "hunt"
    try:
        main(["--target", srv.base_url, "--openapi", _spec(tmp_path, srv.base_url),
              "--out", str(out), "--model", "scripted", "--max-steps", "6"])
    finally:
        srv.stop()

    record = json.loads((out / "hunt.json").read_text(encoding="utf-8"))
    evidence = record.get("evidence") or []
    assert evidence, "hunt 有 VERIFIED 证据却一条也没记"

    states = []
    for item in evidence:
        cid = str(item["capsule_id"])
        path = out / "capsules" / (cid.split(":")[-1] + ".json")
        assert path.is_file(), f"hunt.json 指向不存在的证据：{cid}"
        states.append(_offline(path))
    assert "FAILED" not in states, f"离线重判翻案了：{states}"

    from veripen_core.evidence import load_capsule, order_chain, verify_chain

    caps = [load_capsule(out / "capsules" / (str(e["capsule_id"]).split(":")[-1] + ".json"))
            for e in evidence]
    report = verify_chain(order_chain(caps))
    assert report.ok, report.breaks
    assert report.length == len(evidence)


# ── platform：每个资产的证据自洽，报告里的份数不多不少 ──────────
def test_platform_evidence_is_self_contained(tmp_path: Path) -> None:
    from veripen_cli.cmd_platform import main

    a, b = _lab(), _lab()
    try:
        spec = _spec(tmp_path, a.base_url)
        out = tmp_path / "pf"
        rc = main(["--targets", f"{a.base_url},{b.base_url}", "--openapi", spec,
                   "--out", str(out), "--db", str(tmp_path / "pf.db")])
    finally:
        a.stop()
        b.stop()
    assert rc == 0

    report = (out / "platform-report.md").read_text(encoding="utf-8")
    from veripen_core.evidence import load_capsule

    for i in (1, 2):
        sub = out / f"asset-{i}"
        doc = json.loads((sub / "findings.json").read_text(encoding="utf-8"))
        reported = [f for f in doc["findings"] if f.get("outcome") == "reported"]
        ids = {str((f.get("evidence") or {}).get("capsule_id")) for f in reported}
        assert ids, "该资产有结论却没有证据指针"
        for cid in ids:
            path = sub / "capsules" / (cid.split(":")[-1] + ".json")
            assert path.is_file(), f"资产 {i} 的证据不在盘上：{cid}"
            assert load_capsule(path).capsule_id == cid
        # 报告那一列必须是"结论真正引用的份数"，不是目录里的文件数
        row = next(line for line in report.splitlines() if line.startswith(f"| {i} | `"))
        assert f"| {len(ids)} |" in row, f"报告份数与证据指针不一致：{row}"
