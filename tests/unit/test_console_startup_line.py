"""B243 · 控制台启动行要能分清"没有数据"与"没跑过"。

`out/` 整个目录**不进版本库**（`git ls-files out` = 0），所以干净 clone 上第一次
`veripen console` 必然指到一个不存在的产物文件。旧行为两种情况都只印：

```
数据：0 条（0 已确认 / 0 未验证，其中 blocked 0）
来源：C:\\repo\\out\\b30\\hunt.json
```

客户分不清"我已经跑过、只是没发现漏洞"和"我还没跑过任何东西"——这两种情况
下一步动作完全不同。
"""

from __future__ import annotations

from veripen_cli.cmd_console import artifact_note


def test_missing_artifact_says_run_something_first(tmp_path) -> None:  # noqa: ANN001
    note = artifact_note(str(tmp_path / "nope" / "hunt.json"))
    assert "不存在" in note, note
    assert "veripen run" in note and "veripen hunt" in note, note


def test_existing_artifact_adds_no_noise(tmp_path) -> None:  # noqa: ANN001
    art = tmp_path / "hunt.json"
    art.write_text("{}", encoding="utf-8")
    assert artifact_note(str(art)) == "", "文件在就别加话"


def test_directory_is_not_an_artifact(tmp_path) -> None:  # noqa: ANN001
    """指向目录时也要说清：否则读者会以为产物里一条发现都没有。"""
    assert "目录" in artifact_note(str(tmp_path))
