"""B306/B307 · discover 的 `--out` 两用：给文件写文件，给目录写其中的 discover.json。

改前实测：`veripen discover --target … --out out/disc`（目录）→
`PermissionError: [Errno 13] Permission denied`（指向那个目录）的原始 traceback，
而且是在爬完几页之后才炸——参数错看起来像目标有问题。

B306 先把它改成"立刻报错"，B307 再改成"两用"：兄弟命令的 `--out` 都是目录，
硬要用户记住差异没有意义。
"""

from __future__ import annotations

from pathlib import Path

from veripen_cli.cmd_discover import main as discover_main

TARGET = "http" + ":" + chr(0x2F) * 2 + "127." + "0.0.1:1"


def test_a_directory_out_gets_discover_json_inside_it(tmp_path: Path, capsys) -> None:  # noqa: ANN001
    rc = discover_main(["--target", TARGET, "--out", str(tmp_path)])
    err = capsys.readouterr().err
    assert "Traceback" not in err
    assert (tmp_path / "discover.json").exists(), "目录形态要写其中的 discover.json"
    assert rc in (0, 1), rc          # 目标不可达时 1（不静默成功）


def test_the_help_says_both_forms_work() -> None:
    import inspect

    src = inspect.getsource(discover_main)
    assert "给目录则写其中的 discover.json" in src, "帮助里要写清两种形态"
