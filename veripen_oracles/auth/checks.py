"""认证清单的非 JWT 部分（B9）：OAuth / mTLS / Session 的**检查单 + 半自动判定**。

这三类不能全自动，所以本模块只做两件事：
1. 把"该测什么"变成**可执行的数据**（每条带判据与自动化档位）；
2. 对能自动的三条（O1 回调、O5 code 重放、S1 会话固定）给出**纯函数判定**。

判定函数一律只读 trace（状态码、Location、body），不看时钟、不发请求。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

# ══════════════════════════════════════════════════════════════════
# 检查单项（20 §3–§5）
# ══════════════════════════════════════════════════════════════════
@dataclass(frozen=True)
class CheckItem:
    id: str
    title: str
    #: auto = 本批已实现判定；semi = 需双账号/解码；manual = 需环境（CRL/OCSP/IdP）
    automation: str
    verdict: str  # 判据一句话
    why_manual: str = ""  # 手工项必须写"为什么手工"，不许写"太麻烦"

    def to_row(self) -> dict[str, str]:
        return {
            "id": self.id,
            "title": self.title,
            "automation": self.automation,
            "verdict": self.verdict,
            "why_manual": self.why_manual,
        }


OAUTH_CHECKS: tuple[CheckItem, ...] = (
    CheckItem("O1", "redirect_uri 校验", "auto",
              "OOB 收到回调且 Location 里的 code 可对；只看 Location，不看 body 回显"),
    CheckItem("O2", "state 缺失或可预测", "auto", "去掉 state 重放授权请求是否仍被接受"),
    CheckItem("O3", "nonce 未校验", "semi", "重放 id_token 是否被接受（需解码 id_token）"),
    CheckItem("O4", "PKCE 降级", "auto", "有 challenge 时用 plain 或无 challenge 换 token 是否成功"),
    CheckItem("O5", "code 可重放", "auto", "同一 code 换两次：第二次成功即漏洞"),
    CheckItem("O6", "token 交换越权", "semi", "用 A 的 code 换 B 的 token（需双账号）"),
    CheckItem("O7", "隐式流残留", "auto", "response_type=token 是否仍被支持"),
    CheckItem("O8", "scope 提升", "auto", "请求未授权 scope 是否被授予"),
)

MTLS_CHECKS: tuple[CheckItem, ...] = (
    CheckItem("M1", "客户端证书缺失", "auto", "被拒且原因是 certificate required"),
    CheckItem("M2", "过期证书", "auto", "被拒"),
    CheckItem("M3", "自签/非受信 CA", "auto", "被拒且原因是 unknown ca"),
    CheckItem("M4", "SAN/CN 不匹配", "auto", "被拒"),
    CheckItem("M5", "弱签名算法（SHA1）", "auto", "被拒"),
    CheckItem("M6", "证书吊销未检查", "manual", "需 CRL/OCSP 环境",
              why_manual="靶场未部署 CRL/OCSP，驳回原因无法区分"),
    CheckItem("M7", "双向认证只验单向", "auto", "不带证书也能通 = 单向冒充双向"),
)

SESSION_CHECKS: tuple[CheckItem, ...] = (
    CheckItem("S1", "会话固定（fixation）", "auto", "登录前后 session id 变化（不变 = 漏洞）"),
    CheckItem("S2", "Cookie 标志", "auto", "HttpOnly / Secure / SameSite 缺失即 info/low"),
    CheckItem("S3", "登出未失效", "auto", "登出后旧 cookie 仍可用"),
    CheckItem("S4", "并发会话无上限", "semi", "同账号 N 会话全部有效（阈值）"),
    CheckItem("S5", "CSRF token 可预测/可复用", "semi", "换会话后旧 token 仍被接受"),
)

SAML_CHECKS: tuple[CheckItem, ...] = (
    CheckItem("X1", "签名包裹（XSW）", "manual", "未覆盖", "需构造多断言 XML，工具化成本高"),
    CheckItem("X2", "XXE", "manual", "未覆盖", "需解析器行为差异，本期不做"),
    CheckItem("X3", "断言重放", "manual", "未覆盖", "需 IdP 配合"),
    CheckItem("X4", "未签名断言被接受", "manual", "未覆盖", "需手工改 XML + 真实 IdP"),
)

ALL_CHECKS: tuple[CheckItem, ...] = OAUTH_CHECKS + MTLS_CHECKS + SESSION_CHECKS + SAML_CHECKS


# ══════════════════════════════════════════════════════════════════
# 三条可自动判定的纯函数（只读 trace）
# ══════════════════════════════════════════════════════════════════
@dataclass(frozen=True)
class ProbeResult:
    id: str
    verdict: str  # VERIFIED | REFUTED | INCONCLUSIVE
    reason: str
    evidence: Mapping[str, Any] = field(default_factory=dict)


def judge_redirect_uri(
    *, oob_hit: bool, location_ok: bool, control_location_ok: bool
) -> ProbeResult:
    """O1：只看 Location 与 OOB 回调；**不看 body 回显**（20 §3 末段）。"""
    if not control_location_ok:
        return ProbeResult("O1", "INCONCLUSIVE", "control_not_redirecting",
                           {"oob_hit": oob_hit})
    if oob_hit and location_ok:
        return ProbeResult("O1", "VERIFIED", "redirect_uri_unvalidated",
                           {"oob_hit": True, "location_ok": True})
    return ProbeResult("O1", "REFUTED", "redirect_uri_checked", {"oob_hit": oob_hit})


def judge_code_replay(*, first_ok: bool, second_ok: bool) -> ProbeResult:
    """O5：第二次成功才是漏洞；两次都要 trace（20 §3 取证纪律）。"""
    if not first_ok:
        return ProbeResult("O5", "INCONCLUSIVE", "first_exchange_failed")
    if second_ok:
        return ProbeResult("O5", "VERIFIED", "authorization_code_replayable")
    return ProbeResult("O5", "REFUTED", "code_single_use")


def judge_session_fixation(*, before_id: str, after_id: str) -> ProbeResult:
    """S1：登录前后 session id 不变 = 固定会话。"""
    if not before_id or not after_id:
        return ProbeResult("S1", "INCONCLUSIVE", "no_session_cookie")
    if before_id == after_id:
        return ProbeResult("S1", "VERIFIED", "session_not_rotated",
                           {"session_len": len(after_id)})
    return ProbeResult("S1", "REFUTED", "session_rotated")


def judge_mtls_handshake(*, handshake_ok: bool, error_reason: str | None,
                         client_cert_requested: bool) -> ProbeResult:
    """M 系列：握手失败要**区分原因**（20 §4 取证要点）。"""
    del client_cert_requested  # M7 单独判：不请求证书 = 单向冒充双向
    if handshake_ok:
        return ProbeResult("M1", "VERIFIED", "no_client_cert_accepted",
                           {"reason": None})
    if not error_reason:
        return ProbeResult("M1", "INCONCLUSIVE", "reason_unknown")
    if "certificate required" in error_reason.lower():
        return ProbeResult("M1", "REFUTED", "cert_required", {"reason": error_reason})
    if "unknown ca" in error_reason.lower():
        # 用了自签证书：M1 其实没被测到（测的是 M3）
        return ProbeResult("M1", "INCONCLUSIVE", "measured_M3_not_M1", {"reason": error_reason})
    return ProbeResult("M1", "INCONCLUSIVE", "reason_not_accepted", {"reason": error_reason})


def judge_mtls_one_way(*, client_cert_requested: bool) -> ProbeResult:
    """M7：服务端压根没请求客户端证书 → 双向认证是假的。"""
    if client_cert_requested:
        return ProbeResult("M7", "REFUTED", "client_cert_requested")
    return ProbeResult("M7", "VERIFIED", "one_way_only")


# ══════════════════════════════════════════════════════════════════
# 覆盖矩阵（20 §7：0 命中的标签必须列出来，不藏）
# ══════════════════════════════════════════════════════════════════
def coverage_matrix(
    *, oracle_hits: Mapping[str, int], checks: tuple[CheckItem, ...] = ALL_CHECKS
) -> dict[str, Any]:
    """把"测了哪些 / 没测哪些"汇成一张矩阵；**空的一列也要出现**。"""
    by_automation: dict[str, int] = {}
    for item in checks:
        by_automation[item.automation] = by_automation.get(item.automation, 0) + 1
    zero = sorted(name for name, hits in oracle_hits.items() if hits == 0)
    return {
        "checks": [item.to_row() for item in checks],
        "by_automation": dict(sorted(by_automation.items())),
        "oracle_hits": dict(sorted(oracle_hits.items())),
        "zero_hit_oracles": zero,
        "manual_count": by_automation.get("manual", 0),
    }


__all__ = [
    "ALL_CHECKS",
    "CheckItem",
    "MTLS_CHECKS",
    "OAUTH_CHECKS",
    "ProbeResult",
    "SAML_CHECKS",
    "SESSION_CHECKS",
    "coverage_matrix",
    "judge_code_replay",
    "judge_mtls_handshake",
    "judge_mtls_one_way",
    "judge_redirect_uri",
    "judge_session_fixation",
]
