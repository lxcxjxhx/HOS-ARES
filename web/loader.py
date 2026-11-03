"""控制台装载器：把一轮真实运行的产物映射成 `ConsoleState`。

三条纪律（H07 B37 / H02 §?）：
1. **只读映射，不改判定**：`VERIFIED` 就是 `reported`，不做"看起来像"的补正；
2. **确定性**：同一份产物两次装载，payload 逐字节相同（遍历按 oracle id 排序）；
3. **空输入安全**：文件缺失/为空 → 空状态，页面走空态，不抛异常、不塞假数据。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from web.console import ConsoleState, Row

#: 严重度只在已验证行上有意义；这里只做"oracle → 分档"的展示映射，不参与判定。
SEVERITY: Mapping[str, str] = {
    "sqli_bool_diff": "high",
    "sqli_error_based": "high",
    "xss_reflected_ctx": "medium",
    "xss_stored_probe": "high",
    "idor_cross_account": "high",
    "open_redirect": "low",
    "path_traversal": "high",
    "ssrf_oob": "high",
    "jwt_replay": "medium",
    "jwt_suite": "medium",
    "rate_limit_absent": "low",
    "bola_functional": "high",
    "mass_assignment": "medium",
    "cors_misconfig": "low",
    "graphql_introspection": "low",
}

#: 判定域的四态 → 控制台行状态。**只做映射**：不在表里的值一律当"未确认"，不许升级。
_OUTCOME: Mapping[str, str] = {
    "VERIFIED": "reported",
    "INCONCLUSIVE": "undetermined",
    "BLOCKED": "blocked",
    "NOT_RUN": "not_run",
    "ERROR": "error",
    "REFUTED": "undetermined",   # 判否也是"有结论"，但对"发现列表"而言不成立
}

#: 命中数：一行 = 一次观测。blocked 没跑完 → None（未执行），不能记 0。
_HITS: Mapping[str, int | None] = {
    "reported": 1,
    "undetermined": 0,
    "not_run": None,
    "error": None,
    "blocked": None,
}


def _row(oracle: str, verdict: str, *, reason: str, seq: int, asset: str) -> Row:
    outcome = _OUTCOME.get(verdict.upper(), "undetermined")
    reason_code = f"{verdict.upper()}: {reason}" if reason else verdict.upper()
    return Row(
        finding_id="F-" + str(seq).zfill(3),
        title=f"{oracle} {'在 ' + asset + ' 上成立' if outcome == 'reported' else '未确认'}",
        severity=SEVERITY.get(oracle, "medium") if outcome == "reported" else "",  # type: ignore[arg-type]
        oracle_id=oracle,
        asset_id=asset,
        confidence=1.0 if outcome == "reported" else 0.0,
        route_strength="contract",
        route_ref=None,          # 产物里没有绑定 ref：不编一个（H2 / 零地址）
        replay_status="NEVER",
        outcome=outcome,
        reason_code=reason_code,
        hits=_HITS[outcome],
        capsule_id=f"sha256:{oracle}" if outcome == "reported" else None,
    )


def load_record(record: Mapping[str, Any]) -> ConsoleState:
    """把一轮运行的 JSON 记录映射成控制台状态（纯函数，无 IO）。"""
    asset = str(record.get("target_host") or "unknown")
    rows: list[Row] = []
    seq = 0

    confirmed = record.get("findings") or {}
    if isinstance(confirmed, Mapping):
        for oracle in sorted(confirmed):
            seq += 1
            verdict = confirmed[oracle]
            verdict = verdict if isinstance(verdict, str) else "VERIFIED"
            rows.append(_row(oracle, verdict, reason="", seq=seq, asset=asset))

    others = record.get("not_confirmed") or {}
    if isinstance(others, Mapping):
        for oracle in sorted(others):
            seq += 1
            detail = others[oracle]
            if isinstance(detail, Mapping):
                verdict = str(detail.get("verdict") or "INCONCLUSIVE")
                reason = str(detail.get("reason") or "")
            else:
                verdict, reason = "INCONCLUSIVE", str(detail)
            rows.append(_row(oracle, verdict, reason=reason, seq=seq, asset=asset))

    verified = sum(1 for r in rows if r.verdict == "verified")
    engagement = {
        "id": str(record.get("engagement_id") or "ENG-ARTIFACT"),
        "asset": asset,
        "scope": "只读验证（来自运行产物）",
        "confirmed": verified,
        "total": len(rows),
        "decision": record.get("decision"),
        "reason": record.get("reason"),
        "bindings": record.get("bindings"),
    }
    return ConsoleState(findings=rows, engagement=engagement)


def load_file(path: str | Path) -> ConsoleState:
    """读产物文件；不存在/空/坏 JSON 都退化成空状态（页面显示空态，比塞假数据诚实）。"""
    p = Path(path)
    if not p.is_file():
        return ConsoleState()
    try:
        record = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return ConsoleState()
    if not isinstance(record, Mapping):
        return ConsoleState()
    return load_record(record)


def summarize(state: ConsoleState) -> dict[str, Any]:
    rows: Sequence[Row] = state.findings
    verified = sum(1 for r in rows if r.verdict == "verified")
    return {
        "total": len(rows),
        "verified": verified,
        "unverified": len(rows) - verified,
        "blocked": sum(1 for r in rows if r.verdict == "blocked"),
        "asset": state.engagement.get("asset", "（无数据）"),
    }


__all__ = ["SEVERITY", "load_file", "load_record", "summarize"]
