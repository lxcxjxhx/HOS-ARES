"""本地 TLS 夹具：让 ``https`` 分支能被**真握手**验证，而不是只被 scheme 检查覆盖。

为什么需要它：``transport.connect`` 的 https 分支写的是
``ssl.create_default_context()``——不降级、不关校验。这话在代码里是真的，
但在此之前**一次都没跑过**：11 条 transport 测试全走 http，bench 全走 http。
没跑过的分支等于没有分支。

三条纪律（都是为了让"验证"这件事本身可信）：

1. **证书覆盖 IP SAN**：连的是 ``127.0.0.1``，不是域名。证书里没有
   ``IP:127.0.0.1`` 就会被主机名校验拒掉——那说明我们测的是"能不能连"，
   而不是"校验有没有被绕过"。
2. **只通过 ``SSL_CERT_FILE`` 注入信任**：不碰系统信任库、不改全局 ssl 状态。
   ``create_default_context()`` 每次建连都重读该变量，所以作用域是一次调用。
3. **另有一条"不信任必须失败"的测试**：证明校验真的在生效。
   只有"能连上"的测试，无法区分"校验通过"和"校验被关掉了"。
"""

from __future__ import annotations

import datetime as _dt
import ipaddress
import ssl
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID


def make_cert(
    directory: Path,
    *,
    host_ip: str | None = None,
    host_dns: str | None = None,
    days: int = 1,
    name_hint: str = "lab-cert",
) -> tuple[Path, Path]:
    """生成一对自签证书，返回 ``(cert, key)``。

    ``host_ip`` / ``host_dns`` 决定 SAN：连 IP 的用 IP SAN，连域名（含 SNI）的用 DNS SAN。
    ``days`` 可为负 —— 负值产出**已过期**的证书，用来区分"过期"与"不受信"
    这两种不同的校验失败（它们的错误文本不同，处置也不同）。
    """
    directory.mkdir(parents=True, exist_ok=True)
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "veripen-lab")])
    now = _dt.datetime.now(_dt.timezone.utc)

    san: list[x509.GeneralName] = []
    if host_ip:
        san.append(x509.IPAddress(ipaddress.ip_address(host_ip)))
    if host_dns:
        san.append(x509.DNSName(host_dns))

    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - _dt.timedelta(days=abs(days) + 1))
        .not_valid_after(now - _dt.timedelta(days=1) if days < 0 else now + _dt.timedelta(days=days))
        .add_extension(x509.SubjectAlternativeName(san), critical=False)
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )

    cert_path = directory / f"{name_hint}.pem"
    key_path = directory / f"{name_hint}-key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    return cert_path, key_path


def server_context(cert_path: Path, key_path: Path) -> ssl.SSLContext:
    """服务端上下文。**不关校验**——服务端没有"校验客户端"这回事，除非要 mTLS。"""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(certfile=str(cert_path), keyfile=str(key_path))
    return ctx


__all__ = ["make_cert", "server_context"]
