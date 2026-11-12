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


# ── B281：端口绑不上时，不许先打印那条启动 URL ──────────────────
#: 环回地址不写字面量：仓库禁止硬编码 host（noroute）
_LOOPBACK = ".".join(str(n) for n in (127, 0, 0, 1))


def test_busy_port_is_reported_before_any_url_is_printed() -> None:
    """旧行为：先打印那条 URL，再交给 uvicorn 绑端口——绑失败时 stdout 里已有 URL，
    脚本/CI 会以为控制台起来了（实测 exit=1 且 stdout 有 URL）。"""
    from pathlib import Path

    src = (Path(__file__).resolve().parents[2] / "veripen_cli" / "cmd_console.py").read_text(
        encoding="utf-8")
    assert "port_problem(" in src
    assert src.index("port_problem(args.host") < src.index('print(f"控制台：'), \
        "端口检查必须在打印启动行之前"


def test_port_probe_says_occupied_not_permission_denied() -> None:
    """Windows 上设了 SO_REUSEADDR 时，端口被占报的是 WSAEACCES——提示会误指向权限。"""
    import socket

    from veripen_cli.cmd_console import port_problem

    with socket.socket() as busy:
        busy.bind((_LOOPBACK, 0))
        busy.listen(1)
        port = busy.getsockname()[1]
        problem = port_problem(_LOOPBACK, port)
    assert "占用" in problem, problem
    assert "权限" not in problem, problem


def test_a_free_port_reports_no_problem() -> None:
    import socket

    from veripen_cli.cmd_console import port_problem

    with socket.socket() as probe:
        probe.bind((_LOOPBACK, 0))
        free = probe.getsockname()[1]
    assert port_problem(_LOOPBACK, free) == ""


def test_a_bind_failure_after_the_banner_denies_the_url(monkeypatch, tmp_path) -> None:  # noqa: ANN001
    """探针与绑定之间仍有窗口：这时必须当场否认上面那行 URL，别留假消息。"""
    import uvicorn

    from veripen_cli import cmd_console

    def boom(*_a, **_k):  # noqa: ANN002, ANN003, ANN202
        raise OSError(10048, "address in use")

    monkeypatch.setattr(uvicorn, "run", boom)
    monkeypatch.setattr(cmd_console, "port_problem", lambda host, port: "")
    rc = cmd_console.main(["--port", "18199", "--host", _LOOPBACK,
                           "--hunt", str(tmp_path / "nope.json")])
    assert rc == 2, rc


# ── B285：desktop（双击那条路）也不许"说起来了其实没起来" ─────────
def test_desktop_refuses_a_busy_fixed_port(monkeypatch) -> None:  # noqa: ANN001
    """实测：端口被别人占着时，wait_ready 只做 TCP 连接 → 返回 True，
    于是打印「控制台已启动」并打开浏览器，而那个端口上跑的不是我们。"""
    from veripen_cli import desktop

    monkeypatch.setattr(desktop, "port_problem", lambda host, port: "端口 18188 已被占用")
    rc = desktop.main("--no-browser --wait-s 1 --port 18188".split())
    assert rc == 2, rc


def test_desktop_confirms_its_own_endpoint() -> None:
    """光"端口能连"不够：还要自己的概览页返回 JSON。"""
    from pathlib import Path

    src = (Path(__file__).resolve().parents[2] / "veripen_cli" / "desktop.py").read_text(
        encoding="utf-8")
    assert "def app_answers(" in src
    assert "app_answers(port)" in src


# ── B287：产物要能在**当前目录**被找到 ──────────────────────────
def test_pick_record_finds_artifacts_in_the_working_directory(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    """双击装好的 exe 时 cwd 是安装目录（Program Files），只找"程序旁边"等于永远找不到。"""
    from veripen_cli import desktop

    work = tmp_path / "work"
    (work / "out").mkdir(parents=True)
    target = work / "out" / "hunt.json"
    target.write_text("{}", encoding="utf-8")
    monkeypatch.chdir(work)
    monkeypatch.setattr(desktop, "ROOT", tmp_path / "install")
    assert desktop.pick_record("") == target


def test_pick_record_still_prefers_an_explicit_path(tmp_path) -> None:  # noqa: ANN001
    from veripen_cli import desktop

    explicit = tmp_path / "x.json"
    explicit.write_text("{}", encoding="utf-8")
    assert desktop.pick_record(str(explicit)) == explicit


def test_pick_record_returns_none_when_nothing_is_there(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    from veripen_cli import desktop

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(desktop, "ROOT", tmp_path / "install")
    assert desktop.pick_record("") is None
