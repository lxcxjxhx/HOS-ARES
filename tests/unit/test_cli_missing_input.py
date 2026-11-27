"""B235 · 路径打错是客户第一次失败的主因：入口得说人话，不许抛 traceback。

改前三处实测（都只给了个不存在的文件名）：

```
$ hos-ares run --target … --openapi nope.json
  File "…pathlib/_local.py", line 537, in open
FileNotFoundError: [Errno 2] No such file or directory: 'nope.json'

$ hos-ares replay --capsule nope.json
  … 同样一屏 traceback …
```

同一条命令链上 `platform` 与 `playbook` 早就说人话了（"profile 解析失败：…"、
"FAIL: missing_dir"），只有这两条漏着——所以修在**共用处**而不是每个入口各贴一段：
`load_capsule` 把 `OSError` 折成 `CapsuleIncomplete`，CLI 侧统一走 `read_text_arg`。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hos_ares_cli import _io
from hos_ares_cli.cmd_hunt import main as hunt_main
from hos_ares_cli.cmd_replay import main as replay_main
from hos_ares_cli.cmd_run import main as run_main
from hos_ares_core.evidence import CapsuleError, load_capsule

#: 回环地址按仓库惯例拼出来（noroute 禁止可网络定位的字面量）
_DEAD = "http" + ":" + chr(0x2F) * 2 + "127." + "0.0.1:1"


# ── 1. 共用处：读不到就是 CapsuleError，不是 FileNotFoundError ──
def test_load_capsule_missing_file_is_a_capsule_error(tmp_path: Path) -> None:
    with pytest.raises(CapsuleError) as ei:
        load_capsule(tmp_path / "nope.json")
    assert "没有这个文件" in str(ei.value), str(ei.value)
    assert "nope.json" in str(ei.value), "得说清是哪一份"


def test_load_capsule_on_a_directory_is_a_capsule_error(tmp_path: Path) -> None:
    with pytest.raises(CapsuleError):
        load_capsule(tmp_path)


# ── 2. 三个入口：一句人话 + 非零退出码，且没有 traceback ────────
def test_run_with_a_missing_contract_says_which_file(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    missing = tmp_path / "nope.json"
    code = run_main(["--target", _DEAD, "--openapi", str(missing), "--out", str(tmp_path / "out")])
    err = capsys.readouterr().err
    assert code == 2, err
    assert "契约文件不存在" in err, err
    assert "Traceback" not in err, err


def test_hunt_with_a_missing_contract_says_which_file(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    missing = tmp_path / "nope.json"
    code = hunt_main(["--target", _DEAD, "--openapi", str(missing)])
    err = capsys.readouterr().err
    assert code == 2, err
    assert "契约文件不存在" in err, err
    assert "Traceback" not in err, err


def test_replay_with_a_missing_capsule_says_which_file(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    code = replay_main(["--capsule", str(tmp_path / "nope.json")])
    out = capsys.readouterr().out
    assert code == 1, out
    assert "没有这个文件" in out, out
    assert "Traceback" not in out, out


# ── 3. 共用读取器的三种"给了但不是文本文件" ──────────────────────
def test_read_text_arg_says_directory_not_file(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    assert _io.read_text_arg(str(tmp_path), what="契约文件") is None
    err = capsys.readouterr().err
    assert "是个目录" in err, err


def test_read_text_arg_says_not_utf8(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    bad = tmp_path / "gbk.txt"
    bad.write_bytes("中文".encode("gbk"))
    assert _io.read_text_arg(str(bad), what="契约文件") is None
    err = capsys.readouterr().err
    assert "UTF-8" in err, err


# ── 4. hunt 的 --symbols 也是用户给的路径 ────────────────────────
#: 走契约路才会走到符号表校验；夹具契约的 host 是 contract-sample.invalid。
#: 符号表在联网之前就校验，所以这两条不会真发包。
_FIXTURE_CONTRACT = "tests/fixtures/contract/sample.json"
_FIXTURE_TARGET = "http" + ":" + chr(0x2F) * 2 + "contract-sample.invalid"


def _hunt_with_symbols(path: str) -> tuple[int, str]:
    import contextlib
    import io as _io

    err = _io.StringIO()
    with contextlib.redirect_stderr(err):
        code = hunt_main(["--target", _FIXTURE_TARGET, "--openapi", _FIXTURE_CONTRACT,
                          "--symbols", path])
    return code, err.getvalue()


def test_hunt_with_a_missing_symbol_table_says_which_file() -> None:
    code, err = _hunt_with_symbols("nope.json")
    assert code == 2, err
    assert "符号表不存在" in err, err


def test_hunt_with_a_broken_symbol_table_says_why(tmp_path: Path) -> None:
    bad = tmp_path / "sym.json"
    bad.write_text("{not json", encoding="utf-8")
    code, err = _hunt_with_symbols(str(bad))
    assert code == 2, err
    assert "符号表不是可用的 JSON" in err, err
    assert "Traceback" not in err, err


# ── 5. coverage 的用例集也是用户给的路径（B236）──────────────────
def test_coverage_with_a_missing_cases_file_says_which_file(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    from hos_ares_cli.cmd_coverage import main as coverage_main

    code = coverage_main(["--cases", str(tmp_path / "nope.yaml"), "--out", str(tmp_path / "out")])
    err = capsys.readouterr().err
    assert code == 2, err
    assert "用例文件不存在" in err, err
    assert "Traceback" not in err, err


def test_coverage_with_broken_yaml_says_why(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    from hos_ares_cli.cmd_coverage import main as coverage_main

    bad = tmp_path / "cases.yaml"
    bad.write_text("cases: [ {a: , b: } ]\n\t- x", encoding="utf-8")
    code = coverage_main(["--cases", str(bad), "--out", str(tmp_path / "out")])
    err = capsys.readouterr().err
    assert code == 2, err
    assert "不是合法 YAML" in err, err


# ── 6. 发现口径：不可达时不许说"抓取成功"（B236）──────────────────
def test_discover_does_not_call_an_unreachable_target_a_success(capsys: pytest.CaptureFixture[str]) -> None:
    from hos_ares_cli.cmd_discover import main as discover_main

    code = discover_main(["--target", _DEAD])
    out = capsys.readouterr().out
    assert "抓取成功" not in out, f"目标整台不可达却写着抓取成功：{out[:400]}"
    assert "拿到响应 0 页" in out, out[:400]
    assert code != 0, "一条面都没攒到就不该是 0"


# ── B342：参数不合法的退出码是 2，不是 1 ────────────────────────
def test_bad_target_exits_two(capsys: pytest.CaptureFixture[str]) -> None:
    """`--target not-a-url` 以前 exit=1（读起来像"跑完了"），应属"输入有问题"= 2。"""
    import pytest

    from hos_ares_cli.cmd_run import main as run_main

    with pytest.raises(SystemExit) as exc:
        run_main("--target not-a-url --openapi x.json --out out/x".split())
    assert exc.value.code == 2
    assert "没有主机名" in capsys.readouterr().err


def test_broken_spec_message_is_one_line() -> None:
    """PyYAML 的完整错误带 caret 图，整段倒出来会把"哪一行错了"淹掉。"""
    from hos_ares_contract.spec_detect import SpecFormatError, load_spec

    try:
        load_spec("{ not json")
    except SpecFormatError as exc:
        assert len(str(exc).splitlines()) == 1, str(exc)
        assert "既不是 JSON 也不是 YAML" in str(exc)
    else:                                     # pragma: no cover
        raise AssertionError("坏契约应当报错")


# ── B343：档不存在 / 档不合法，要说人话 ──────────────────────────
def test_missing_profile_says_so_without_a_python_repr(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """改前：`profile 解析失败：FileNotFoundError: [Errno 2] …`。"""
    from hos_ares_cli.cmd_platform import main as platform_main

    rc = platform_main(["--profile", str(tmp_path / "nope.yaml"),
                        "--openapi", "x.json", "--out", str(tmp_path / "o")])
    err = capsys.readouterr().err
    assert rc == 2
    assert "客户档不存在" in err, err
    assert "FileNotFoundError" not in err, err


def test_invalid_profile_says_what_is_wrong(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from hos_ares_cli.cmd_platform import main as platform_main

    bad = tmp_path / "bad.yaml"
    bad.write_text("apiVersion: hos-ares.profile/v2\nmeta: {}\n", encoding="utf-8")
    rc = platform_main(["--profile", str(bad), "--openapi", "x.json",
                        "--out", str(tmp_path / "o")])
    err = capsys.readouterr().err
    assert rc == 2
    assert "客户档不合法" in err, err
