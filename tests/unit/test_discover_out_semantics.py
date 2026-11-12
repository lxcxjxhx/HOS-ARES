"""B306 · `--out` 在 discover 里是**文件**，在 run/hunt/platform 里是目录。

实测（改前）：`veripen discover --target … --out out/disc`（目录）→
`PermissionError: [Errno 13] Permission denied`（指向那个目录）的原始 traceback，
而且是在爬完几页之后才炸——参数错看起来像目标有问题。
"""

from __future__ import annotations

from pathlib import Path

from veripen_cli.cmd_discover import main as discover_main

TARGET = "http" + ":" + chr(0x2F) * 2 + "127." + "0.0.1:1"


def test_a_directory_out_is_refused_before_crawling(tmp_path: Path, capsys) -> None:  # noqa: ANN001
    rc = discover_main(["--target", TARGET, "--out", str(tmp_path)])
    err = capsys.readouterr().err
    assert rc == 2, rc
    assert "需要一个**文件**路径" in err, err
    assert "Traceback" not in err


def test_the_help_says_it_is_a_file() -> None:
    import inspect

    src = inspect.getsource(discover_main)
    assert "不是目录" in src, "帮助里要写清这一点"
