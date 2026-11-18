"""B400 · `hunt` 的机器产物也要带能力边界。"""

from __future__ import annotations

from pathlib import Path

SEP = chr(0x2F)

def test_hunt_json_carries_capability_limits(tmp_path: Path) -> None:
    """`hunt.md` 有边界、`hunt.json` 没有——机器消费者读到的仍是"全测过了"（B400）。"""
    import json

    from lab.app import App
    from lab.net_server import LabServer
    from veripen_cli import cmd_hunt

    srv = LabServer(App(secure=False)).start()
    try:
        spec = tmp_path / "s.json"
        spec.write_text(json.dumps({
            "openapi": "3.0.0", "info": {"title": "lab", "version": "1"},
            "servers": [{"url": srv.base_url}],
            "paths": {f"{SEP}api{SEP}me": {"get": {"x-veripen-tags": ["protected_read"]}}},
        }), encoding="utf-8")
        out = tmp_path / "hunt"
        cmd_hunt.main(["--target", srv.base_url, "--openapi", str(spec), "--out", str(out)])
        record = json.loads((out / "hunt.json").read_text(encoding="utf-8"))
        checks = {item["check"] for item in record["capability_limits"]}
        # 钉**稳定**的成员：观测受限（M5/M7）与选不中（csp_absent/cors_misconfig）。
        # 不钉 O4/O8/S3 这类"会被实现掉"的项——钉它们等于每次实现都要改测试。
        assert {"M7", "M5", "csp_absent", "cors_misconfig"} <= checks, checks
    finally:
        srv.stop()
