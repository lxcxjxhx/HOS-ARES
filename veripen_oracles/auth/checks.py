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


def judge_pkce(*, control_ok: bool, probe_status: int) -> ProbeResult:
    """O4：PKCE 是否真的校验 code_verifier。

    先看对照（正确 verifier 能不能换到 token）——换不到说明这条面根本不走 PKCE，
    判不了；再看错误 verifier：**200 = 没校验**（截到授权码就能换 token）。
    """
    if not control_ok:
        return ProbeResult("O4", "INCONCLUSIVE", "no_pkce_semantics")
    if probe_status == 200:
        return ProbeResult("O4", "VERIFIED", "pkce_not_enforced")
    if probe_status in (400, 401, 403):
        return ProbeResult("O4", "REFUTED", "pkce_enforced")
    return ProbeResult("O4", "INCONCLUSIVE", "pkce_effect_ambiguous",
                       {"probe_status": probe_status})


def judge_logout(*, control_ok: bool, probe_status: int) -> ProbeResult:
    """S3：登出后旧会话还能用吗。

    顺序讲究：先看对照（登出前带着会话能不能取到东西）——取不到说明这条面
    根本没有会话语义，判不了；否则再看探针：**200 = 登出没失效**。
    """
    if not control_ok:
        return ProbeResult("S3", "INCONCLUSIVE", "no_session_semantics")
    if probe_status == 200:
        return ProbeResult("S3", "VERIFIED", "session_survived_logout")
    if probe_status in (400, 401, 403):
        return ProbeResult("S3", "REFUTED", "session_revoked")
    return ProbeResult("S3", "INCONCLUSIVE", "logout_effect_ambiguous",
                       {"probe_status": probe_status})


def judge_certificate_defect(*, error_reason: str, scheme: str) -> ProbeResult:
    """M2/M3/M4/M5：从"客户端为什么没连上"读出证书缺陷类型。

    顺序讲究：先看是不是 https（http 上谈不上证书），再看错误文本。
    文本匹配用下划线归一化后的形式——OpenSSL 的宏名带下划线，人写的带空格（B385 的教训）。
    """
    if scheme not in ("https", ""):
        return ProbeResult("M2", "INCONCLUSIVE", "not_https", {"scheme": scheme})
    if not error_reason:
        return ProbeResult("M2", "REFUTED", "cert_ok")
    low = error_reason.lower().replace("_", " ")
    if "certificate verify failed" not in low:
        # 连不上但原因与证书无关（拒绝连接、路由不通…）：判不了
        return ProbeResult("M2", "INCONCLUSIVE", "not_a_certificate_error",
                           {"error": error_reason[:120]})
    checks = (
        ("M2", "cert_expired", ("expired", "not yet valid")),
        ("M4", "cert_san_mismatch", ("hostname mismatch", "ip address mismatch",
                                     "doesn't match", "no appropriate commonname")),
        ("M5", "cert_weak_signature", ("md too weak", "ca md too weak", "sha1")),
        ("M3", "cert_untrusted", ("self-signed", "unable to get local issuer",
                                  "unable to verify", "unknown ca")),
    )
    for code, reason, needles in checks:
        if any(needle in low for needle in needles):
            return ProbeResult(code, "VERIFIED", reason, {"error": error_reason[:160]})
    return ProbeResult("M3", "INCONCLUSIVE", "cert_error_unclassified",
                       {"error": error_reason[:160]})


def judge_code_format(*, malformed_accepted: tuple[str, ...],
                      probe_count: int) -> ProbeResult:
    """O7：换码端点看不看授权码的形状。

    一支被受理就够了——那说明"任何字符串都能进下一步"。把被受理的**形状名**带出去，
    读者才知道是漏了长度、字符集还是两者都漏。
    """
    if probe_count <= 0:
        return ProbeResult("O7", "INCONCLUSIVE", "no_probe")
    if malformed_accepted:
        return ProbeResult("O7", "VERIFIED", "code_format_unchecked",
                           {"accepted_shapes": list(malformed_accepted)})
    return ProbeResult("O7", "REFUTED", "code_format_checked")


def judge_cookie_flags(*, missing: tuple[str, ...], https: bool) -> ProbeResult:
    """S2：Cookie 三件套（HttpOnly / Secure / SameSite）。

    `Secure` 只在 https 上有意义：http 目标缺它不是缺陷（浏览器也不会发回来）。
    一条都不缺 → 判否；缺任一条 → 成立，并把缺的项原样带出去（读者要知道缺哪个）。
    """
    del https  # 调用方已经把不适用项从 missing 里剔掉了
    if not missing:
        return ProbeResult("S2", "REFUTED", "cookie_flags_complete")
    return ProbeResult("S2", "VERIFIED", "cookie_flags_missing", {"missing": list(missing)})


def judge_state_param(*, control_redirects: bool, control_echoed: bool,
                       probe_accepted: bool) -> ProbeResult:
    """O2：`state` 是否被强制与回传。

    顺序讲究：先看对照会不会跳（不跳 = 这个面没有跳转语义），再看"不带 state 会不会被受理"，
    最后才看回传。**受理本身就够定罪**——回传与否是另一件事。
    """
    if not control_redirects:
        return ProbeResult("O2", "INCONCLUSIVE", "control_not_redirecting")
    if probe_accepted:
        return ProbeResult("O2", "VERIFIED", "state_not_enforced",
                           {"control_echoed": control_echoed})
    if control_echoed:
        return ProbeResult("O2", "REFUTED", "state_enforced")
    return ProbeResult("O2", "INCONCLUSIVE", "state_not_observed")


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
    # OpenSSL 的原文是 `TLSV13_ALERT_CERTIFICATE_REQUIRED`（下划线），而人的写法是
    # "certificate required"（空格）。不归一化就永远匹配不上——B385 实测到：
    # 真实的双向认证靶场被判成 `reason_not_accepted`，而它其实是对的。
    normalized = error_reason.lower().replace("_", " ")
    if "certificate required" in normalized:
        return ProbeResult("M1", "REFUTED", "cert_required", {"reason": error_reason})
    if "unknown ca" in normalized:
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
#: 已经有**判定函数**的检查项（B375）。矩阵里的 `auto` 一直是"本批打算自动"，
#: 而不是"已经有判定"——15 条 auto 里只有这 5 条真有函数，其余 10 条连判定都没写。
#: 把两者分开列，读者才不会把"清单里有"读成"能测"。
JUDGES: Mapping[str, str] = {
    "O1": "judge_redirect_uri",
    "O2": "judge_state_param",
    "O5": "judge_code_replay",
    "O7": "judge_code_format",
    "O4": "judge_pkce",
    "S1": "judge_session_fixation",
    "S3": "judge_logout",
    "S2": "judge_cookie_flags",
    "M1": "judge_mtls_handshake",
    # 四类证书缺陷共用一条判定函数（它按错误文本分类）
    "M2": "judge_certificate_defect",
    "M3": "judge_certificate_defect",
    "M4": "judge_certificate_defect",
    "M5": "judge_certificate_defect",
    "M7": "judge_mtls_one_way",
}


#: 判定函数有、但**客户端侧观测不到**的检查项（B386）。
#: 列出来是为了让后人别重试：这不是"还没接"，是"从这一侧测不出来"。
UNMEASURABLE: Mapping[str, str] = {
    "M7": (
        "Python 的 ssl 不暴露 CertificateRequest；TLS 1.3 下"
        "「服务端可选地请求证书」与「压根不请求」都会握手成功——客户端无法区分"
    ),
    "M5": (
        "客户端**信任**了弱签名证书就不会报错：实测用 openssl 造一张 ECDSA-SHA1 证书、"
        "把它当受信 CA 去连，Python 的默认上下文照样握手成功（错误文本里没有 md too weak）。"
        "判定函数已能分类该文本，但没有可观测的触发条件——要测得换一侧（服务端/抓包）"
    ),
}


#: 判据本身要求**多步序列**（登出→复用、发码→换码、发 token→越权使用），
#: 而 harness 的一个变体只发**一次**请求，`plan()` 也拿不到上一次的响应（B392）。
#: 这些不是"不会写"，是"当前执行模型装不下"——要么加多步支持，要么承认测不了。
NEEDS_SEQUENCE: Mapping[str, str] = {
    "O8": "先用限定 scope 换 token，再拿它访问超范围功能——两跳且第二跳要用第一跳的产物",
}


def coverage_matrix(
    *, oracle_hits: Mapping[str, int], checks: tuple[CheckItem, ...] = ALL_CHECKS
) -> dict[str, Any]:
    """把"测了哪些 / 没测哪些"汇成一张矩阵；**空的一列也要出现**。

    `auto` 还要再分一刀（B375）：`implemented=True` 才真有判定函数。
    没有的那一类单独计数并列名——它们有靶场计划、没有代码，不能算覆盖。
    """
    by_automation: dict[str, int] = {}
    rows: list[dict[str, str]] = []
    missing_judge: list[str] = []
    for item in checks:
        by_automation[item.automation] = by_automation.get(item.automation, 0) + 1
        row = item.to_row()
        row["judge"] = JUDGES.get(item.id, "")
        # 三档互斥（B392）：有判定函数 / 只有清单（写得出但还没写）/ 需要多步序列
        # （执行模型装不下）。以前后两者混在一个名单里，读起来像"还有 7 条没写"。
        if item.automation == "auto" and not row["judge"] and item.id not in NEEDS_SEQUENCE:
            missing_judge.append(item.id)
        rows.append(row)
    zero = sorted(name for name, hits in oracle_hits.items() if hits == 0)
    auto = [r for r in rows if r["automation"] == "auto"]
    return {
        "checks": rows,
        "by_automation": dict(sorted(by_automation.items())),
        "auto_total": len(auto),
        "auto_implemented": len([r for r in auto if r["judge"]]),
        #: 声明 auto 但没有判定函数——**这不是覆盖，是待办**
        "auto_without_judge": sorted(missing_judge),
        "unmeasurable": dict(sorted(UNMEASURABLE.items())),
        "needs_sequence": dict(sorted(NEEDS_SEQUENCE.items())),
        "oracle_hits": dict(sorted(oracle_hits.items())),
        "zero_hit_oracles": zero,
        "manual_count": by_automation.get("manual", 0),
    }


__all__ = [
    "ALL_CHECKS",
    "NEEDS_SEQUENCE",
    "UNMEASURABLE",
    "JUDGES",
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
    "judge_certificate_defect",
    "judge_logout",
    "judge_pkce",
    "judge_code_format",
    "judge_cookie_flags",
    "judge_state_param",
    "judge_session_fixation",
]
