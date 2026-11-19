"""B385 · M1：服务端是否真的要求客户端证书。

两件不同的事，分开测：

1. **判定层**：OpenSSL 的真实原文是 `TLSV13_ALERT_CERTIFICATE_REQUIRED`（下划线），
   而 `judge_mtls_handshake` 里写的是 `"certificate required"`（空格）——不归一化
   就永远匹配不上。实测：一个**正确**要求客户端证书的靶场被判成 `reason_not_accepted`。
2. **端到端**：单向 TLS 的 https 面上，不带客户端证书照样能连 → `VERIFIED /
   no_client_cert_accepted`（这个面并没有做双向认证）。

`expect_mtls` 默认 False：不声明"这里应该是双向认证"就不出结论——否则每个普通
https 接口都会被记成"未要求客户端证书"，那是纯误报。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

SEP = chr(0x2F)
PREFIX = f"{SEP}api{SEP}me"

#: 真实 OpenSSL 文本（Python ssl 抛出来的就是这一句）
_OPENSSL_CERT_REQUIRED = "[SSL: TLSV13_ALERT_CERTIFICATE_REQUIRED] tlsv13 alert certificate required"


def test_certificate_required_text_is_matched_after_normalising() -> None:
    from hos_ares_oracles.auth.checks import judge_mtls_handshake

    result = judge_mtls_handshake(handshake_ok=False, error_reason=_OPENSSL_CERT_REQUIRED,
                                  client_cert_requested=True)
    assert result.verdict == "REFUTED", result
    assert result.reason == "cert_required", result


def test_unknown_ca_is_not_counted_as_m1() -> None:
    """自签证书报 unknown ca：测到的是 M3（证书链），别冒充 M1。"""
    from hos_ares_oracles.auth.checks import judge_mtls_handshake

    result = judge_mtls_handshake(handshake_ok=False,
                                  error_reason="[SSL: CERTIFICATE_VERIFY_FAILED] unknown ca",
                                  client_cert_requested=True)
    assert result.verdict == "INCONCLUSIVE", result
    assert result.reason == "measured_M3_not_M1", result


def test_connection_reset_is_inconclusive_not_a_conclusion() -> None:
    """被 RST 掉（没收到告警）：只能说"原因不明"，不许当成"要求了证书"。"""
    from hos_ares_oracles.auth.checks import judge_mtls_handshake

    result = judge_mtls_handshake(handshake_ok=False,
                                  error_reason="ConnectionResetError: [Errno 10054]",
                                  client_cert_requested=False)
    assert result.verdict == "INCONCLUSIVE", result
    assert result.reason == "reason_not_accepted", result


def test_without_expectation_the_oracle_stays_silent() -> None:
    """没声明 mTLS 就不出结论（否则每个 https 面都会变成"高危"）。"""
    from types import SimpleNamespace

    from hos_ares_oracles.auth.mtls import MtlsRequired
    from hos_ares_oracles.common.harness import Pair

    trace = SimpleNamespace(status=200, headers={}, error=None, body=b"{}",
                            scheme="https", is_normal=lambda: True)
    pair = Pair(variant=SimpleNamespace(role="probe"), baseline=trace, trace=trace)
    judgement = MtlsRequired().judge([pair])
    assert judgement.reason == "mtls_not_declared", judgement


@pytest.fixture()
def tls_certs(tmp_path: Path):  # noqa: ANN201
    from lab.net_server import LOOPBACK
    from lab.tls import make_cert

    certs = make_cert(tmp_path, host_ip=LOOPBACK)
    return certs


def test_one_way_tls_face_accepts_a_client_without_a_certificate(tls_certs, tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    """单向 https 面：不带客户端证书也能 200 → 这个面没有双向认证。"""
    from lab.app import App
    from lab.net_server import LabServer

    from hos_ares_cli.cmd_run import main as run_main

    monkeypatch.setenv("SSL_CERT_FILE", str(tls_certs[0]))
    srv = LabServer(App(secure=False), tls=tls_certs).start()
    try:
        spec = tmp_path / "s.json"
        spec.write_text(json.dumps({
            "openapi": "3.0.0", "info": {"title": "lab", "version": "1"},
            "servers": [{"url": srv.base_url}],
            "paths": {PREFIX: {"get": {"x-hos-ares-tags": ["protected_read"]}}},
        }), encoding="utf-8")
        out = tmp_path / "run"
        run_main(["--target", srv.base_url, "--openapi", str(spec), "--out", str(out)])
        out_declared = tmp_path / "run-declared"
        run_main(["--target", srv.base_url, "--openapi", str(spec), "--out", str(out_declared),
                  "--oracle-params", json.dumps({"mtls_required": {"expect_mtls": True}})])
        rows = [(f["oracle"], f["outcome"], f["reason_code"])
                for f in json.loads((out / "findings.json").read_text(encoding="utf-8"))["findings"]]
    finally:
        srv.stop()
        os.environ.pop("SSL_CERT_FILE", None)

    # 默认不声明 → 不出结论；这条钉的是"默认静默"，避免 https 面集体误报
    assert ("mtls_required", "undetermined", "mtls_not_declared") in rows, rows

    declared = [(f["oracle"], f["outcome"], f["reason_code"])
                for f in json.loads((out_declared / "findings.json").read_text(encoding="utf-8"))["findings"]]
    # 声明之后，**401 的授权面也能出结论**（B387）：基线允许 4xx + probe 用 any_response
    assert ("mtls_required", "reported", "no_client_cert_accepted") in declared, declared


def test_declared_mtls_face_without_client_cert_is_verified() -> None:
    """声明了 mTLS 却放行无证书客户端 → 才是缺陷（这条钉 VERIFIED 分支）。"""
    from types import SimpleNamespace

    from hos_ares_oracles.auth.mtls import MtlsRequired
    from hos_ares_oracles.common.harness import Pair

    trace = SimpleNamespace(status=200, headers={}, error=None, body=b"{}",
                            scheme="https", is_normal=lambda: True)
    pair = Pair(variant=SimpleNamespace(role="probe"), baseline=trace, trace=trace)
    judgement = MtlsRequired(expect_mtls=True).judge([pair])
    assert judgement.verdict.value == "VERIFIED", judgement
    assert judgement.reason == "no_client_cert_accepted", judgement
