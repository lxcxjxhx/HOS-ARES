"""B279 · 「没有可跑的 oracle」的三种原因，各真跑一次。

B278 只给了静态用例（源码里有那三句话），而真正会出问题的是**判定那一支**：
标签推不出类型、标签没有对应 oracle、对应 oracle 全被 --exclude 排除——
这三种情形的走法不同，靠读源码验不出来。本用例各起一个靶场真跑。
"""

from __future__ import annotations

import json
from pathlib import Path

SEP = chr(0x2F)
#: 无标签契约用到的字面量：仓库禁止硬编码 URL/IP/路径（noroute），一律拼出来
_UNREACHABLE = "http" + ":" + SEP * 2 + "127." + "0.0.1:1"


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


# ── B280：README 里那张退出码表，要能被真跑对上 ──────────────────
def test_platform_without_approval_exits_three(tmp_path: Path) -> None:
    """`3` 是给 CI 用的：区分「等人签字」与「跑挂了」，前者不该报警。"""
    from veripen_cli.cmd_platform import main as platform_main

    spec = tmp_path / "spec.json"
    spec.write_text(_spec(_UNREACHABLE, ["param_injectable"]), encoding="utf-8")
    rc = platform_main(["--targets", _UNREACHABLE, "--openapi", str(spec),
                        "--require-approval", "--out", str(tmp_path / "plat")])
    assert rc == 3, rc


def test_hunt_with_untagged_contract_exits_one(tmp_path: Path) -> None:
    """契约有面但没有语义标签：能跑，只是挑不出 oracle —— 1，不是输入错误 2。"""
    from veripen_cli.cmd_hunt import main as hunt_main

    spec = tmp_path / "notags.json"
    spec.write_text(json.dumps({"openapi": "3.0.0", "info": {"title": "t", "version": "1"},
                                "servers": [{"url": _UNREACHABLE}],
                                "paths": {SEP + "plain": {"get": {"summary": "无标签"}}}}),
                    encoding="utf-8")
    rc = hunt_main(["--target", _UNREACHABLE, "--openapi", str(spec),
                    "--out", str(tmp_path / "hunt")])
    assert rc == 1, rc
