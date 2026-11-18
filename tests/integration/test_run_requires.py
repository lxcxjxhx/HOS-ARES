"""B378 · `run` 补齐两条 requires 入口：`--oob` 与 `--token-secondary`。

改前：`hunt` 早就有这两个入口，`run` 一个都没有 —— 于是 `ssrf_oob`（requires
`oob:listener`）与 `idor_cross_account` / `bola_functional`（requires `auth:secondary`）
在**主命令**里永远满足不了；严格模式下只能落 `missing_input:*`。

改这条时撞到两个坑，各配一条断言：
1. 闭包里名字叫 `primary`/`secondary` 会被下面 `for primary in faces` 覆写成
   RouteBinding，`.strip()` 直接崩成 `oracle_crash:AttributeError`；
2. `unmet_requirements` 里 `auth:secondary` 硬编码 False（"当前 CLI 没有第二身份入口"），
   只加 flag 不改那张表的话，flag 是装饰品。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

SEP = chr(0x2F)


def _run(lab: Any, tmp_path: Path, tag: str, *extra: str) -> list[tuple[str, str]]:
    from hos_ares_cli.cmd_run import main as run_main

    tmp_path.mkdir(parents=True, exist_ok=True)
    spec = tmp_path / "s.json"
    spec.write_text(json.dumps({
        "openapi": "3.0.0", "info": {"title": "lab", "version": "1"},
        "servers": [{"url": lab.base_url}],
        "paths": {f"{SEP}api{SEP}items": {"get": {"x-hos_ares-tags": [tag]}}},
    }), encoding="utf-8")
    out = tmp_path / "run"
    run_main(["--target", lab.base_url, "--openapi", str(spec), "--out", str(out),
              "--strict-requires", *extra])
    data = json.loads((out / "findings.json").read_text(encoding="utf-8"))
    return [(f["oracle"], f["reason_code"]) for f in data["findings"]]


@pytest.fixture()
def lab():  # noqa: ANN201
    from lab.app import App
    from lab.net_server import LabServer

    srv = LabServer(App(secure=False)).start()
    try:
        yield srv
    finally:
        srv.stop()


def test_oob_flag_satisfies_the_listener_requirement(lab, tmp_path: Path) -> None:  # noqa: ANN001
    before = _run(lab, tmp_path / "a", "url_fetch")
    assert ("ssrf_oob", "missing_input:oob:listener") in before, before
    after = _run(lab, tmp_path / "b", "url_fetch", "--oob", "oob.invalid")
    assert not [r for o, r in after if o == "ssrf_oob" and r.startswith("missing_input:")], after


def test_secondary_token_satisfies_the_second_identity_requirement(lab, tmp_path: Path) -> None:  # noqa: ANN001
    before = _run(lab, tmp_path / "a", "object_read")
    assert ("idor_cross_account", "missing_input:auth:secondary") in before, before
    after = _run(lab, tmp_path / "b", "object_read",
                 "--token", "t-main", "--token-secondary", "t-other")
    reasons = [r for o, r in after if o == "idor_cross_account"]
    assert reasons and not reasons[0].startswith("missing_input:"), reasons
    # 闭包晚绑定那个坑：给了 token 之后不许再崩
    assert not reasons[0].startswith("oracle_crash:"), reasons


def test_second_identity_reaches_a_judgement_not_a_missing_symbol(lab, tmp_path: Path) -> None:  # noqa: ANN001
    """`--token-secondary` 只满足 requires 还不够（B379）。

    oracle 用 `account="STRANGER"` 这个**符号**表达"换个人发"，而值要由符号表给
    （bench/demo 里都是 `STRANGER=secondary`）。只加凭据入口、不加符号入口时，
    idor 会先过 requires、再撞 `symbol_missing:STRANGER`——半个能力。
    """
    import urllib.request

    def _token(account: str) -> str:
        with urllib.request.urlopen(f"{lab.base_url}/api/login?account={account}", timeout=5) as r:
            return json.loads(r.read())["token"]

    from hos_ares_cli.cmd_run import main as run_main

    spec = tmp_path / "s.json"
    tmp_path.mkdir(parents=True, exist_ok=True)
    spec.write_text(json.dumps({
        "openapi": "3.0.0", "info": {"title": "lab", "version": "1"},
        "servers": [{"url": lab.base_url}],
        "paths": {f"{SEP}api{SEP}items": {"get": {"x-hos_ares-tags": ["object_read"]}}},
    }), encoding="utf-8")
    out = tmp_path / "run"
    run_main(["--target", lab.base_url, "--openapi", str(spec), "--out", str(out),
              "--strict-requires",
              "--token", _token("primary"),
              "--token-secondary", _token("secondary")])
    rows = [(f["oracle"], f["reason_code"])
            for f in json.loads((out / "findings.json").read_text(encoding="utf-8"))["findings"]]
    reason = dict(rows)["idor_cross_account"]
    assert not reason.startswith("missing_input:"), reason
    assert not reason.startswith("symbol_missing:"), reason
    assert not reason.startswith("oracle_crash:"), reason


def test_oob_listen_turns_ssrf_into_a_real_finding(lab, tmp_path: Path) -> None:  # noqa: ANN001
    """B383：`--oob-listen` 起真监听器后，`ssrf_oob` 第一次能判出正样本。

    改前产品侧完全不知道 OOB：判定规则只活在 `tests/lab/transport_oob.py` 里，
    `CoreTransport` 从不打 `oob_seen`，于是这个 oracle 在主命令上只能判否。
    """
    from hos_ares_cli.cmd_run import main as run_main

    spec = tmp_path / "s.json"
    tmp_path.mkdir(parents=True, exist_ok=True)
    spec.write_text(json.dumps({
        "openapi": "3.0.0", "info": {"title": "lab", "version": "1"},
        "servers": [{"url": lab.base_url}],
        "paths": {f"{SEP}api{SEP}fetch": {"get": {"x-hos_ares-tags": ["url_fetch"]}}},
    }), encoding="utf-8")

    out = tmp_path / "run"
    run_main(["--target", lab.base_url, "--openapi", str(spec), "--out", str(out),
              "--oob-listen"])
    rows = [(f["oracle"], f["outcome"], f["reason_code"])
            for f in json.loads((out / "findings.json").read_text(encoding="utf-8"))["findings"]]
    assert ("ssrf_oob", "reported", "server_fetched_oob") in rows, rows
