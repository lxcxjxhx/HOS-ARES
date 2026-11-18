"""B248 · 判定码的**人话表**。

报告里"原因"列如果只有一个 `marker_not_fired`，客户读不出任何东西：它既不是英文
也不是中文，只是一段机器标识。这张表把码翻成一句话，用在：

- `hos-ares run` 报告 §3（未能验证段）
- `hos-ares hunt` 报告的"未确认（带原因）"

**表里没有的码就原样显示**——不猜、不编，也不假装所有码都被覆盖了。
"""

from __future__ import annotations

from typing import Sequence


GLOSS: dict[str, str] = {
    # ── 缺输入/对照 ────────────────────────────────────────────
    "missing_variant": "缺对照变体：目标没给出这条判定必需的那条响应，判不了",
    "baseline_abnormal": "基线响应异常：正常请求本身就不正常，不能据此下结论（下一步：看同一条面的 HTTP 状态与错误原文；401/403 的基线要用 --token 提供一个合法会话）",
    "control_not_denied": "对照请求没被拒：无法证明目标在验签/鉴权——没有证据支持成立，判否",
    "render_failed": "渲染失败：没拿到可用于判定的页面，判不了（不是「没有漏洞」）",
    # ── 差分类 ────────────────────────────────────────────────
    "boolean_differential_holds": "布尔差分成立：真/假条件的响应指纹不同，且无害对照两条一致",
    "no_differential": "真/假条件的响应没有差异：布尔差分不成立——判否",
    "error_fingerprint_triggered": "注入载荷触发了数据库错误指纹，对照与基线都没有",
    "no_error_fingerprint": "注入后没有出现数据库错误指纹：报错注入不成立——判否",
    "no_fingerprint": "响应里没有可用于区分的指纹——判否",
    "generic_error_page": "只拿到通用错误页，区分不出载荷是否生效——没有证据支持成立，判否",
    "probe_not_reproduced": "复测没有复现同一现象：现象不稳定，按判否处理（不作为结论）",
    "partial_success": "只出现部分迹象，不足以判定",
    # ── 鉴权/越权 ─────────────────────────────────────────────
    "object_level_authz_missing": "低权限身份读到了别人的对象",
    "functional_authz_missing": "低权限身份调到了高权限功能",
    "token_validation_defect": "无效签名被拒，但存在被接受的变体且返回了身份标记",
    "control_not_denied_signature": "对照请求没被拒：无法证明目标在验签",
    "no_discriminating_field": "响应里没有「只有该身份才看得到」的字段：分不出是不是越权，判不了",
    # ── 注入/输出 ─────────────────────────────────────────────
    "reflected_unescaped": "参数被原样反射且未转义",
    "stored_unescaped": "写入后原样回显且未转义（存储型）",
    "marker_not_fired": "探针标记没有出现（注入点未触发）——判否",
    "readonly_field_accepted": "只读字段被接受写入",
    "write_rejected": "写请求被拒（没有产生副作用）——这一条守住了",
    # ── 读文件/重定向/SSRF ────────────────────────────────────
    "path_traversal": "读到了期望范围之外的文件",
    "open_redirect": "能被重定向到外部地址",
    "no_external_redirect": "没有发生外部重定向——判否",
    "server_fetched_oob": "服务端发起了外部请求（带外通道收到回调）",
    "no_callback": "带外通道没有收到回调：目标没有发起外部请求——判否",
    "origin_reflected": "Origin 被回显",
    "origin_not_reflected": "Origin 没有被回显：服务端不回显来源——这一条守住了",
    # ── GraphQL / 限流 ────────────────────────────────────────
    "introspection_disabled": "GraphQL introspection 已关闭：拿不到 schema——这一条守住了",
    "introspection_and_authz_missing": "introspection 开放且无鉴权",
    "throttling_observed": "观测到限流生效：这个面有速率限制——这一条守住了",
    "variants_rejected": "全部变体都被拒绝：防护在起作用（这是「测了、守住了」，不是「没测」）",
    # ── 判定码补全（B370）──────────────────────────────────────
    # 这些是 oracle **自己产出**的码：它们会原样进客户的「原因」列。表里缺一条，
    # 报告里就出现一个裸机器词（实测：安全靶上 `control_also_differs` 旁边两行都有
    # 人话，就它没有）。判据由 tests/unit/test_glossary_covers_oracle_codes.py 扫源码钉住。
    # OAuth / 会话 / mTLS
    "redirect_uri_unvalidated": "重定向地址没被校验：带外通道收到回调，Location 也指向了外部",
    "redirect_uri_checked": "重定向地址被校验：既没回调，Location 也没跳出去",
    "control_not_redirecting": "对照请求自己就没跳转：无法证明目标在校验地址",
    "first_exchange_failed": "第一次授权码交换就没成功，重放无从验证",
    "authorization_code_replayable": "同一个授权码能换第二次：码可复用",
    "code_single_use": "授权码一次性：第二次交换被拒",
    "no_session_cookie": "没拿到会话 cookie：会话固定无从验证",
    "session_not_rotated": "登录前后 session id 不变（固定会话）",
    "session_rotated": "登录后 session id 换了（不是固定会话）",
    "no_client_cert_accepted": "不带客户端证书也能握手：双向认证是假的",
    "cert_required": "服务端要求客户端证书，无证书握手被拒（真的在双向认证）",
    "reason_unknown": "握手失败但读不出原因（没有错误串）",
    "reason_not_accepted": "握手失败的原因不在已知几类里，判不了",
    "measured_M3_not_M1": "用的是自签证书：测到的是 M3（证书信任），不是 M1",
    "client_cert_requested": "服务端请求了客户端证书（不是单向冒充）",
    "one_way_only": "服务端压根没请求客户端证书：双向认证是假的",
    "all_variants_rejected": "JWT 的全部变体都被拒绝：算法混淆/篡改/坏签名都被挡住（这是「测了、守住了」）",
    # 判定链骨架
    "no_control": "oracle 没给出对照变体：缺少「正常时是什么样」的参照",
    "unstable": "同一请求多次结果不一致：目标响应本身不稳定，判不了（下一步：先用 --repeat 复跑确认是不是偶发；目标在抖动时任何结论都不算数）",
    "three_way_failed": "oracle 说看见了洞，但独立三路复算不成立——以不成立为准（这是**实现缺陷**，不是目标缺陷：请把这条带 oracle id 报给我们）",
    # 头/传输
    "fetch_failed": "请求没取到响应（传输层报错）：头相关的项无法判定",
    "not_https": "目标是明文 HTTP：这条判定不适用（不等于没问题）",
    "control_5xx": "对照请求也返回 5xx：目标整体异常，差分不成立",
    # 差分/注入/越权
    "any_url_is_fetched": "任意 URL 都被抓取（连正常对照也抓）：分不出是不是 SSRF",
    "control_also_differs": "对照两路也出现了同样的差异：差异不是载荷造成的",
    "control_also_redirects": "对照也被重定向走：跳转不是载荷造成的",    "control_also_wildcard": "对照也返回通配 Origin：通配不是配置造成的",
    "any_path_returns_content": "任意路径都返回内容（对照也返回）：分不出是不是穿越",
    "account_unusable": "低权账号本身用不了（对照都失败）：鉴权无从验证",
    "admin_function_denied": "低权账号调管理功能被拒：功能级鉴权在生效——这一条守住了",
    "escaped_or_absent": "探针被转义、或压根没出现在响应里（反射点不成立）",
    "escaped_or_stripped": "写入被转义、或在输出时被剥掉（存储点不成立）",
    "baseline_already_privileged": "基线本来就带了这个权限：分不出是不是越权",
    "field_ignored": "只读字段被忽略：写不进去、没有副作用——这一条守住了",
    "control_empty_page": "对照页是空的：写进去了但读不出来，判不了",
    "use_judge_burst": "这项要按并发突发判定，本通道给不了（改走 burst 判定）",
    "preflight_wide_open": "CORS 预检对任意 Origin 都放行",
    "preflight_wide_open_with_credentials": "CORS 预检对任意 Origin 放行，且允许带凭据",
    "wildcard_origin_low": "Origin 被通配放行（证据强度低，未做伪造源验证）",
}


#: 越界事件的原因码（`/ui/bounds` 用）：这两条是 P0 事故的直接说明
SCOPE_CODES = {
    "peer_in_deny": "对端命中拒绝名单：这一发本来就不该出去",
    "peer_not_in_scope": "对端不在已解析的资产地址里：配置与解析对不上",
}


def explain(code: str) -> str:
    """码 → 一句话；不认识就返回空串（调用方原样显示码，不编）。"""
    key = (code or "").strip()
    if key in GLOSS:
        return GLOSS[key]
    if key in SCOPE_CODES:
        return SCOPE_CODES[key]
    # 带参数的码：`symbol_missing:SAFE_FILE` / `SymbolMissing: SAFE_FILE`（异常串是驼峰+空格）
    low = key.lower().replace(" ", "")
    for prefix in ("transport_error:", "transporterror:"):
        if low.startswith(prefix):
            kind = key.split(":", 1)[1].strip()
            return f"连不上目标（{kind}）：先确认端口/证书/代理，再谈覆盖面"
    for prefix in ("symbol_missing:", "symbolmissing:"):
        if low.startswith(prefix):
            tail = key.split(":", 1)[1].strip()
            from hos_ares_oracles.common.harness import symbol_hint  # noqa: PLC0415

            return (f"缺输入符号 {tail}：这类判定必须先拿到它才能构造请求"
                    f"（提供方式：{symbol_hint(tail)}）")
    # missing_face（B416）：多步判据要打的面没找到。以前这条在覆盖页上只有裸模板名，
    # 客户读成「这个功能测过了」——反过来才对：它一个字都没测到。
    if low.startswith("missing_face:"):
        face = key.split(":", 1)[1].strip()
        return (f"要打的面 {face} 在目标上没找到：契约里没声明，或路径不同——"
                f"这条判定一步都没跑，不是「测过了没问题」")
    if key == "no_probe":
        return "这一轮没有发出任何探针，判不了——不代表没问题"
    # scope 越权（B412）
    if key == "scope_not_enforced":
        return "请求的范围超出授权，服务端照样授予：拿到这个 token 就能调原本不该碰的功能——这一条没守住"
    if key == "scope_request_rejected":
        return "授权阶段就拒绝了越权范围请求（没发出 token）：这一条守住了"
    if key == "no_scope_semantics":
        return "连正常范围的令牌也能进受限功能：这条面根本不看范围，判不了「提升」"
    if key == "scope_enforced":
        return "只读范围的 token 调管理功能被拒：服务端按 scope 授权了——这一条守住了"
    if key == "no_token_semantics":
        return "这条面换不到 token（连正常换码都失败）：判不了"
    if key == "scope_effect_ambiguous":
        return "越权访问的响应既不是成功也不是拒绝：看不出 scope 有没有被强制，判不了"
    # PKCE（B411）
    if key == "pkce_not_enforced":
        return "换码时不校验 code_verifier：截到授权码就能换 token——这一条没守住"
    if key == "pkce_enforced":
        return "换码时校验 code_verifier：光有授权码换不出 token——这一条守住了"
    if key == "no_pkce_semantics":
        return "这条面换不到 token（连正确的 verifier 也换不到）：判不了"
    if key == "pkce_effect_ambiguous":
        return "换码响应既不是成功也不是拒绝：看不出 verifier 有没有被校验，判不了"
    # 登出失效（B410）
    if key == "session_survived_logout":
        return "登出之后旧会话仍然可用：拿到过这个会话标识的人可以继续用它——这一条没守住"
    if key == "session_revoked":
        return "登出之后旧会话立即失效：服务端真的吊销了会话——这一条守住了"
    if key == "no_session_semantics":
        return "这条面拿不到会话（登出前就取不到东西）：没有会话语义，判不了"
    if key == "logout_effect_ambiguous":
        return "登出后响应既不是成功也不是拒绝：看不出会话有没有失效，判不了"
    # 证书四类缺陷（B393）
    for code in ("cert_expired", "cert_untrusted", "cert_san_mismatch", "cert_weak_signature"):
        if key == code:
            return {
                "cert_expired": "目标证书已过期：客户端会校验失败，中间人也更容易被用户直接放行",
                "cert_untrusted": "目标证书不受信（自签或链不全）：客户端无法确认对端身份",
                "cert_san_mismatch": "证书名字与访问地址不符：无法证明这就是那台机器",
                "cert_weak_signature": "证书签名算法太弱（如 SHA-1）：可被伪造",
            }[code]
    if key == "cert_ok":
        return "证书在客户端看来正常（未过期、受信、名字匹配）：这一条守住了"
    if key == "not_a_certificate_error":
        return "连接失败与证书无关（拒绝连接/路由不通）：判不了证书好坏"
    if key == "cert_error_unclassified":
        return "证书校验失败，但错误文本对不上已知四类：需要人工看一眼原始错误"
    # 授权码形式（B391）
    if key == "code_format_unchecked":
        return "换码端点不校验授权码的形状：任何字符串都能进下一步，可枚举、可注入"
    if key == "code_format_checked":
        return "换码端点先按形状过滤授权码：这一条守住了"
    # 会话 Cookie 标志（B390）
    if key == "cookie_flags_missing":
        return "会话 cookie 缺安全标志（HttpOnly / Secure / SameSite）：脚本能读到它，跨站也可能被带出去"
    if key == "cookie_flags_complete":
        return "会话 cookie 的 HttpOnly / Secure / SameSite 都设了：这一条守住了"
    if key == "no_set_cookie":
        return "这个响应没有下发 cookie，无从判断标志——「没看到」不是「没问题」"
    # OAuth state（B389）
    if key == "state_not_enforced":
        return "授权请求不带 state 也被受理：别人可以塞自己的回调，把授权码引到他的域（CSRF / 授权码注入）"
    if key == "state_enforced":
        return "服务端强制要求 state 并原样回传：这一条守住了"
    if key == "state_not_observed":
        return "对照没有回传 state，无从判断到底有没有校验——「看不到」不是「没问题」"
    # 声称双向认证但没声明/没要求客户端证书（B385）
    if key == "mtls_not_declared":
        return ("这个面没被声明为「应做双向认证」，因此不判——"
                "普通 https 接口不带客户端证书是正常的（要么在档里声明，要么不测）")
    # 缺输入（B378）：`missing_input:oob:listener` / `missing_input:auth:secondary`——
    # 严格模式（--strict-requires）下由 harness 产出。以前落进"未知码"分支，
    # 客户报告里就是一行裸码 `missing_input:oob:listener`（实测）。
    for prefix in ("missing_input:", "missinginput:"):
        if low.startswith(prefix):
            need = key.split(":", 1)[1].strip()
            # 与 `symbol_missing` 同一条纪律（B406/B407）：说清"怎么给"，否则客户只能来问
            from hos_ares_oracles.common.harness import symbol_hint  # noqa: PLC0415

            head = need.split(":", 1)[0].strip().lower()
            # 两类**不是符号**的缺输入：它们的给法来自命令行开关，不是 `--symbols`
            need_hints = {"auth": "--token-secondary（第二个身份的会话）",
                          "oob": "--oob <回连地址> 或 --oob-listen"}
            hint = need_hints.get(head) or symbol_hint(head.upper())
            return (f"缺输入（{need}）：这条判定要先有它才能跑——"
                    f"「没测」不是「没问题」（提供方式：{hint}）")
    # 工具自己崩了（B364）：以前这类异常连 `类型: 异常文本` 一起塞进原因列，
    # 报告里就出现一行 `RuntimeError: 渲染线程启动失败…`——既不是码也不是人话，
    # 还会随 locale 变。给个稳定码，异常详情仍进日志（cmd_run 打印原文）。
    for prefix in ("oracle_crash:", "oraclecrash:"):
        if low.startswith(prefix):
            kind = key.split(":", 1)[1].strip()
            return (f"这一项没跑成，是工具自身出了问题（{kind}）——"
                    f"不是目标的结论，别读成「没有漏洞」")
    return ""


def explain_with_code(code: str) -> str:
    """``一句话 · `code` ``；不认识就给 ``code``。表格里用这个，机器可对照。"""
    text = explain(code)
    return f"{text} · `{code}`" if text else f"`{code}`"


__all__ = ["GLOSS", "explain", "explain_with_code"]

def exclusion_line(names: Sequence[str], *, why: str) -> str:
    """`已排除 N 类 oracle：…（why；不算缺口，但审计要看得到）`（B330）。

    三路交付各自写过这句话，措辞还不一样（run「按 --exclude 排除」、hunt「已排除」）。
    同一件事就一份措辞，改一处三处一起变。
    """
    if not names:
        return ""
    listed = "、".join(f"`{x}`" for x in names)
    return (f"已排除 {len(names)} 类 oracle：{listed}"
            f"（{why}；不算缺口，但审计要看得到）")
