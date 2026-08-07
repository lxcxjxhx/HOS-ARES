# -*- coding: utf-8 -*-
"""
coop/security.py — 手机-电脑协同安全措施

说明并占位实现三类安全措施：
    1. Token 鉴权：每个请求携带令牌，服务端校验（防未授权访问）
    2. 传输加密：HTTPS / WireGuard VPN（防窃听与篡改）
    3. 防中间人（MITM）：证书校验 / 指纹固定（防伪冒服务器）

本模块为占位实现，使用纯标准库（secrets / hmac / hashlib / ssl）。
"""

from __future__ import annotations

import hmac
import secrets
import ssl
from typing import Optional


# ---------------------------------------------------------------------------
# 1) Token 鉴权
# ---------------------------------------------------------------------------
class TokenAuth:
    """
    Token 鉴权器（占位实现）。

    说明：
        - 手机端与电脑端预先共享一个 token（例如通过 WireGuard 内网 + 一次性交换，
          或通过 .env 中 AGENT_SERVER_TOKEN 配置）。
        - 手机端每个请求在 HTTP 头 `Authorization: Bearer <token>` 中携带。
        - 服务端用恒定时间比较（hmac.compare_digest）校验，避免时序侧信道。
        - 后续可升级为：token 带过期时间 / 刷新机制 / 按设备签发子 token。
    """

    def __init__(self, token: Optional[str] = None) -> None:
        # 未指定时自动生成一个强随机 token（演示用）
        self.token: str = token if token else secrets.token_urlsafe(32)

    def header_value(self) -> str:
        """生成标准 Bearer 鉴权头值。"""
        return f"Bearer {self.token}"

    def check_header(self, authorization: Optional[str]) -> bool:
        """
        校验 HTTP Authorization 头。

        参数：
            authorization: 请求头值，如 "Bearer xxx"。
        返回：
            True 表示鉴权通过。
        """
        if not authorization:
            return False
        scheme, _, value = authorization.partition(" ")
        if scheme.lower() != "bearer":
            return False
        return self._safe_equal(value.strip(), self.token)

    def check(self, provided: Optional[str]) -> bool:
        """校验裸 token 值（不带 Bearer 前缀的快捷方式）。"""
        if not provided:
            return False
        return self._safe_equal(provided, self.token)

    @staticmethod
    def _safe_equal(a: str, b: str) -> bool:
        """恒定时间字符串比较，防时序攻击。"""
        return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


# ---------------------------------------------------------------------------
# 2) 传输加密：HTTPS / WireGuard VPN
# ---------------------------------------------------------------------------
TRANSPORT_SECURITY_DOC = """
传输加密说明
============

推荐分层方案（由内到外）：

A. WireGuard VPN（首选，推荐）：
   - 手机端与电脑端各装 WireGuard，组一个私密虚拟内网；
   - 电脑 Agent Server 仅监听 VPN 内网 IP，不对公网暴露；
   - 所有通信在加密隧道内进行，天然防窃听 / 篡改 / 中间人；
   - 优点：部署简单、性能高、稳定；即使 HTTP 明文在隧道内也安全。

B. HTTPS / TLS（兜底 / 公网场景）：
   - 当无法组建 VPN、必须公网访问时，用 HTTPS 提供传输层加密；
   - 需要为服务器签发证书（自签或 Let's Encrypt），并在手机端做证书校验。

实现占位：
   - 下方 load_ssl_context() 演示如何用标准库 ssl 把 HTTP Server 提升为 HTTPS；
   - WireGuard 属于操作系统/网络层配置，不在本代码库内实现（见 README 部署章节）。
"""


def load_ssl_context(certfile: str, keyfile: str) -> ssl.SSLContext:
    """
    【占位】创建用于 HTTPS 的 SSLContext。

    真实实现建议：
        - certfile / keyfile 为服务器证书与私钥（PEM 格式）；
        - 生产环境证书建议用 Let's Encrypt 等受信任 CA 签发，手机端即可系统级信任；
        - 自签证书场景下，需在手机端导入 CA 并开启主机名校验，见「防中间人」。
    """
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certfile, keyfile)
    return context


# ---------------------------------------------------------------------------
# 3) 防中间人（MITM）
# ---------------------------------------------------------------------------
MITM_SECURITY_DOC = """
防中间人（MITM）说明
====================

目标：防止「假服务器」冒充电脑 Agent Server 窃取任务数据。

手段（按强度递增）：
    1. HTTPS + 受信任 CA 证书：手机端验证服务器证书由可信 CA 签发（默认行为）；
    2. 证书指纹固定（Certificate Pinning）：手机端预置服务器证书的公钥指纹，
       校验实际证书指纹是否一致，即使 CA 被攻破也能防伪冒；
    3. WireGuard VPN 预共享密钥：两端预共享密钥，非密钥持有方无法建立隧道。

实现占位：
    - 下方 verify_server_cert() 演示指纹固定的思路；
    - 真实实现时，在客户端 URL 校验通过后再比对指纹，失败即拒绝连接。
"""


def verify_server_cert(pinned_sha256: str, actual_cert_pem: str) -> bool:
    """
    【占位】证书指纹固定校验。

    参数：
        pinned_sha256: 手机端预置的服务器证书 SHA-256 指纹（十六进制小写）；
        actual_cert_pem: 连接时获取的实际服务器证书 PEM 文本。
    返回：
        True 表示指纹一致，可信任该服务器。
    """
    import hashlib
    import re
    # 取出 PEM 中 DER 内容（去掉 BEGIN/END 行）
    body = re.sub(r"-----BEGIN [^-]+-----|-----END [^-]+-----|\s", "", actual_cert_pem)
    der = bytes.fromhex(body.encode("utf-8").hex())  # 占位：PEM base64 需用 base64 解码
    digest = hashlib.sha256(der).hexdigest()
    return hmac.compare_digest(digest, pinned_sha256)


# ---------------------------------------------------------------------------
# 汇总说明
# ---------------------------------------------------------------------------
SECURITY_OVERVIEW = f"""
安全措施总览
============
1. Token 鉴权      ：{TokenAuth.__doc__.strip()}
2. 传输加密        ：{TRANSPORT_SECURITY_DOC.splitlines()[3] if TRANSPORT_SECURITY_DOC else ''}...
3. 防中间人        ：见 MITM_SECURITY_DOC
"""


if __name__ == "__main__":
    # 验证鉴权器逻辑
    auth = TokenAuth("demo-token-1234")
    print("token:", auth.token)
    print("正确鉴权:", auth.check_header("Bearer demo-token-1234"))
    print("错误鉴权:", auth.check_header("Bearer wrong-token"))
    print("无鉴权  :", auth.check_header(None))
    print()
    print(TRANSPORT_SECURITY_DOC)
    print(MITM_SECURITY_DOC)
