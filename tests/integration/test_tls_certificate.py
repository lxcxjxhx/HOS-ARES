"""B393 · M2/M3/M4/M5：目标证书的四类缺陷，一次请求四种结论。

判据不看证书本身，只看**客户端为什么没连上**——错误文本就是证据：

| 场景 | 客户端看到 | 期望 |
|---|---|---|
| 不信任自签 | `self-signed certificate` | M3 `cert_untrusted` |
| 信任但已过期（`days=-1`） | `certificate has expired` | M2 `cert_expired` |
| 信任但 SAN 是别的 IP | `IP address mismatch` | M4 `cert_san_mismatch` |
| 正常证书 + 信任 | 无错误 | 判否 `cert_ok` |

两处执行模型上的必要补充（都只对该 oracle 生效）：
1. `observes_failures`：**所有请求都带 error 是判据本身**，不能被记成"执行失败"；
2. `expect="any_outcome"`：变体的结果就是观测对象，连失败也算"跑成了"。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

SEP = chr(0x2F)
PREFIX = f"{SEP}api{SEP}me"


def _row(certs: tuple[Path, Path], tmp_path: Path, *, trust: bool) -> dict[str, Any]:
    from lab.app import App
    from lab.net_server import LabServer
    from veripen_cli.cmd_run import main as run_main

    old = os.environ.get("SSL_CERT_FILE")
    if trust:
        os.environ["SSL_CERT_FILE"] = str(certs[0])
    else:
        os.environ.pop("SSL_CERT_FILE", None)
    tmp_path.mkdir(parents=True, exist_ok=True)
    srv = LabServer(App(secure=False), tls=certs).start()
    try:
        spec = tmp_path / "s.json"
        spec.write_text(json.dumps({
            "openapi": "3.0.0", "info": {"title": "lab", "version": "1"},
            "servers": [{"url": srv.base_url}],
            "paths": {PREFIX: {"get": {"x-veripen-tags": ["protected_read"]}}},
        }), encoding="utf-8")
        out = tmp_path / "run"
        run_main(["--target", srv.base_url, "--openapi", str(spec), "--out", str(out)])
        data = json.loads((out / "findings.json").read_text(encoding="utf-8"))
        rows = [f for f in data["findings"] if f["oracle"] == "tls_certificate"]
        assert rows, [(f["oracle"], f["reason_code"]) for f in data["findings"]]
        return rows[0]
    finally:
        srv.stop()
        if old is None:
            os.environ.pop("SSL_CERT_FILE", None)
        else:
            os.environ["SSL_CERT_FILE"] = old


@pytest.fixture()
def certs(tmp_path_factory: pytest.TempPathFactory) -> dict[str, tuple[Path, Path]]:
    from lab.net_server import LOOPBACK
    from lab.tls import make_cert

    base = tmp_path_factory.mktemp("certs")
    return {
        "healthy": make_cert(base / "ok", host_ip=LOOPBACK, days=30),
        "expired": make_cert(base / "exp", host_ip=LOOPBACK, days=-1),
        "mismatch": make_cert(base / "mm", host_ip="10.255.255.1", days=30),
    }


def test_untrusted_certificate_is_m3(certs, tmp_path: Path) -> None:  # noqa: ANN001
    row = _row(certs["healthy"], tmp_path, trust=False)
    assert row["outcome"] == "reported", row
    assert row["reason_code"] == "cert_untrusted", row


def test_expired_certificate_is_m2(certs, tmp_path: Path) -> None:  # noqa: ANN001
    row = _row(certs["expired"], tmp_path, trust=True)
    assert row["outcome"] == "reported", row
    assert row["reason_code"] == "cert_expired", row


def test_san_mismatch_is_m4(certs, tmp_path: Path) -> None:  # noqa: ANN001
    row = _row(certs["mismatch"], tmp_path, trust=True)
    assert row["outcome"] == "reported", row
    assert row["reason_code"] == "cert_san_mismatch", row


def test_healthy_certificate_is_refuted(certs, tmp_path: Path) -> None:  # noqa: ANN001
    row = _row(certs["healthy"], tmp_path, trust=True)
    assert row["outcome"] == "not_run", row
    assert row.get("refuted") is True, row
    assert row["reason_code"] == "cert_ok", row


def test_classification_is_by_error_text() -> None:
    from veripen_oracles.auth.checks import judge_certificate_defect

    assert judge_certificate_defect(
        error_reason="SSLError: certificate verify failed: certificate has expired",
        scheme="https").reason == "cert_expired"
    assert judge_certificate_defect(
        error_reason="SSLError: certificate verify failed: self-signed certificate",
        scheme="https").reason == "cert_untrusted"
    assert judge_certificate_defect(
        error_reason="SSLError: certificate verify failed: IP address mismatch",
        scheme="https").reason == "cert_san_mismatch"
    assert judge_certificate_defect(
        error_reason="SSLError: certificate verify failed: ca md too weak",
        scheme="https").reason == "cert_weak_signature"
    # 与证书无关的失败：判不了，不许当成"证书没问题"
    assert judge_certificate_defect(
        error_reason="ConnectionResetError: [Errno 10054]", scheme="https").verdict == "INCONCLUSIVE"
    # http 面谈不上证书
    assert judge_certificate_defect(error_reason="", scheme="http").verdict == "INCONCLUSIVE"


def test_weak_signature_certificate_is_accepted_by_the_client(tmp_path: Path) -> None:
    """M5 的边界：**受信的**弱签名证书，客户端不会报错。

    用 openssl 造一张 ECDSA-SHA1 证书（`cryptography` 直接拒签 SHA-1），
    把它当受信 CA 去连——实测握手成功，错误文本里没有 `md too weak`。
    所以 M5 记在 `UNMEASURABLE` 里：判定函数能分类这段文本，但没有可观测的触发条件。
    """
    import shutil
    import socket
    import ssl
    import subprocess
    import threading

    from lab.net_server import LOOPBACK

    san = "subjectAltName=IP:" + LOOPBACK
    subject = SEP + "CN=sha1-lab"
    openssl = shutil.which("openssl")
    if not openssl:
        pytest.skip("本机没有 openssl CLI，无法造弱签名证书")

    key = tmp_path / "k.pem"
    crt = tmp_path / "c.pem"
    subprocess.run(  # noqa: S603 - 固定参数，仅用于生成夹具
        [openssl, "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1",
         "-keyout", str(key), "-out", str(crt), "-days", "30", "-nodes", "-sha1",
         "-subj", subject, "-addext", san],
        capture_output=True, check=True,
    )
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(str(crt), str(key))
    lsock = socket.socket()
    lsock.bind((LOOPBACK, 0))
    lsock.listen(1)
    port = lsock.getsockname()[1]

    def _serve() -> None:
        try:
            conn, _ = lsock.accept()
            with ctx.wrap_socket(conn, server_side=True) as tls:
                tls.recv(64)
        except Exception:  # noqa: BLE001 - 服务端报错也是观测的一部分
            pass
        finally:
            lsock.close()

    threading.Thread(target=_serve, daemon=True).start()
    cctx = ssl.create_default_context(cafile=str(crt))
    with socket.create_connection((LOOPBACK, port), timeout=5) as raw:
        with cctx.wrap_socket(raw, server_hostname=LOOPBACK):
            pass  # 能走到这里就说明客户端接受了弱签名
