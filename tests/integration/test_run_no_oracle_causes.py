"""B279 · 「没有可跑的 oracle」的三种原因，各真跑一次。

B278 只给了静态用例（源码里有那三句话），而真正会出问题的是**判定那一支**：
标签推不出类型、标签没有对应 oracle、对应 oracle 全被 --exclude 排除——
这三种情形的走法不同，靠读源码验不出来。本用例各起一个靶场真跑。
"""

from __future__ import annotations

import json
from pathlib import Path

SEP = chr(0x2F)


def _spec(base: str, tags: list[str]) -> str:
    paths = {f"{SEP}api{SEP}search": {"get": {"x-veripen-tags": tags}}}
    return json.dumps({"openapi": "3.0.0", "info": {"title": "lab", "version": "1"},
                       "servers": [{"url": base}], "paths": paths})


def _run(tmp_path: Path, tags: list[str], extra: list[str]) -> int:
    from lab.app import App
    from lab.net_server import LabServer
    from veripen_cli.cmd_run import main as run_main

    lab = LabServer(App(secure=False)).start()
    try:
        spec = tmp_path / "spec.json"
        spec.write_text(_spec(lab.base_url, tags), encoding="utf-8")
        return run_main(["--target", lab.base_url, "--openapi", str(spec),
                         "--out", str(tmp_path / "run"), *extra])
    finally:
        lab.stop()


def test_excluded_cause_is_reachable(tmp_path: Path, capsys) -> None:  # noqa: ANN001
    """把某个面的全部 oracle 都 --exclude 掉：原因是 excluded，下一步指向那条排除。"""
    rc = _run(tmp_path, ["param_injectable"],
              ["--exclude", "sqli_bool_diff,sqli_error_based,xss_dom"])
    captured = capsys.readouterr()
    blob = captured.out + captured.err
    assert rc == 1, blob
    assert "全被 --exclude 排除" in blob, blob
    assert "下一步：对应 oracle 被 --exclude 排除了" in blob, blob


def test_no_oracle_cause_is_reachable(tmp_path: Path, capsys) -> None:  # noqa: ANN001
    """用一个没有 oracle 的标签（object_list）：原因是 no_oracle。"""
    rc = _run(tmp_path, ["object_list"], [])
    captured = capsys.readouterr()
    blob = captured.out + captured.err
    assert rc == 1, blob
    assert "没有对应 oracle" in blob, blob
    assert "下一步：这些标签目前没有对应 oracle" in blob, blob
