"""B248 · 判定码的**人话表**。

报告里"原因"列如果只有一个 `marker_not_fired`，客户读不出任何东西：它既不是英文
也不是中文，只是一段机器标识。这张表把码翻成一句话，用在：

- `veripen run` 报告 §3（未能验证段）
- `veripen hunt` 报告的"未确认（带原因）"

**表里没有的码就原样显示**——不猜、不编，也不假装所有码都被覆盖了。
"""

from __future__ import annotations


GLOSS: dict[str, str] = {
    # ── 缺输入/对照 ────────────────────────────────────────────
    "missing_variant": "缺对照变体：目标没给出这条判定必需的那条响应",
    "baseline_abnormal": "基线响应异常：正常请求本身就不正常，不能据此下结论",
    "control_not_denied": "对照请求没被拒：无法证明目标在验签/鉴权",
    "render_failed": "渲染失败：没拿到可用于判定的页面",
    # ── 差分类 ────────────────────────────────────────────────
    "boolean_differential_holds": "布尔差分成立：真/假条件的响应指纹不同，且无害对照两条一致",
    "no_differential": "真/假条件的响应没有差异",
    "error_fingerprint_triggered": "注入载荷触发了数据库错误指纹，对照与基线都没有",
    "no_error_fingerprint": "注入后没有出现数据库错误指纹",
    "no_fingerprint": "响应里没有可用于区分的指纹",
    "generic_error_page": "只拿到通用错误页，区分不出载荷是否生效",
    "probe_not_reproduced": "复测没有复现同一现象",
    "partial_success": "只出现部分迹象，不足以判定",
    # ── 鉴权/越权 ─────────────────────────────────────────────
    "object_level_authz_missing": "低权限身份读到了别人的对象",
    "functional_authz_missing": "低权限身份调到了高权限功能",
    "token_validation_defect": "无效签名被拒，但存在被接受的变体且返回了身份标记",
    "control_not_denied_signature": "对照请求没被拒：无法证明目标在验签",
    "no_discriminating_field": "响应里没有「只有该身份才看得到」的字段",
    # ── 注入/输出 ─────────────────────────────────────────────
    "reflected_unescaped": "参数被原样反射且未转义",
    "stored_unescaped": "写入后原样回显且未转义（存储型）",
    "marker_not_fired": "探针标记没有出现（注入点未触发）",
    "readonly_field_accepted": "只读字段被接受写入",
    "write_rejected": "写请求被拒（没有产生副作用）",
    # ── 读文件/重定向/SSRF ────────────────────────────────────
    "path_traversal": "读到了期望范围之外的文件",
    "open_redirect": "能被重定向到外部地址",
    "no_external_redirect": "没有发生外部重定向",
    "server_fetched_oob": "服务端发起了外部请求（带外通道收到回调）",
    "no_callback": "带外通道没有收到回调",
    "origin_reflected": "Origin 被回显",
    "origin_not_reflected": "Origin 没有被回显",
    # ── GraphQL / 限流 ────────────────────────────────────────
    "introspection_disabled": "GraphQL introspection 已关闭",
    "introspection_and_authz_missing": "introspection 开放且无鉴权",
    "throttling_observed": "观测到限流生效",
    "variants_rejected": "全部变体都被拒绝",
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
            return f"缺输入符号 {tail}：这类判定必须先拿到它才能构造请求"
    return ""


def explain_with_code(code: str) -> str:
    """``一句话 · `code` ``；不认识就给 ``code``。表格里用这个，机器可对照。"""
    text = explain(code)
    return f"{text} · `{code}`" if text else f"`{code}`"


__all__ = ["GLOSS", "explain", "explain_with_code"]
