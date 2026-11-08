"""控制台装载器：把一轮真实运行的产物映射成 `ConsoleState`。

三条纪律（H07 B37 / H02 §?）：
1. **只读映射，不改判定**：`VERIFIED` 就是 `reported`，不做"看起来像"的补正；
2. **确定性**：同一份产物两次装载，payload 逐字节相同（遍历按 oracle id 排序）；
3. **空输入安全**：文件缺失/为空 → 空状态，页面走空态，不抛异常、不塞假数据。
"""

from __future__ import annotations

import json
import re
from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping, Sequence

from veripen_oracles import ALL_ORACLES
from veripen_oracles.policy import severity_for

from web.console import ConsoleState, Row

#: 严重度：**从 policy 的规则表算**，不在这里另抄一份。
#:
#: 早先这里有一张手写表，和 `veripen_oracles/policy.py` 各写一遍——
#: 两份表迟早给出两个数（open_redirect 一边 low 一边 medium）。
SEVERITY: Mapping[str, str] = {
    str(c.meta.id): severity_for(str(c.meta.id), str(c.meta.severity_rule))[0]
    for c in ALL_ORACLES
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



def _ref_tail(ref: str) -> str:
    """`route://crawl/abc123` → `abc123`（与 run 侧 finding id 的取尾口径一致）。"""
    if not ref:
        return "00000000"
    return ref.rsplit(chr(0x2F), 1)[-1][:8]


def _row(oracle: str, verdict: str, *, reason: str, seq: int, asset: str) -> Row:
    outcome = _OUTCOME.get(verdict.upper(), "undetermined")
    reason_code = f"{verdict.upper()}: {reason}" if reason else verdict.upper()
    return Row(
        finding_id="F-" + str(seq).zfill(3),
        title=f"{oracle} {'在 ' + asset + ' 上成立' if outcome == 'reported' else '未确认'}",
        severity=SEVERITY.get(oracle, "medium") if outcome == "reported" else None,
        oracle_id=oracle,
        asset_id=asset,
        confidence=1.0 if outcome == "reported" else 0.0,
        route_strength="contract",
        route_ref=None,          # 产物里没有绑定 ref：不编一个（H2 / 零地址）
        replay_status="NEVER",
        outcome=outcome,
        reason_code=reason_code,
        hits=_HITS[outcome],
        # 不编 capsule：产物里没有证据指针时就是 None，编一个等于给假证据
        capsule_id=None,
    )


def _from_report_findings(record: Mapping[str, Any], findings: Sequence[Any]) -> ConsoleState:
    """`run` 的 findings.json（列表形态）→ 控制台状态。

    两种产物必须都能打开：`hunt.json` 是 oracle→verdict 的映射，
    `findings.json` 是渲染后的列表。只认前者的话，用户把 `run` 的目录指给
    控制台会看到空白页——而 `run` 才是主命令。
    """
    asset = str(record.get("engagement") or "unknown")
    rows: list[Row] = []
    for i, raw in enumerate(findings, start=1):
        if not isinstance(raw, Mapping):
            continue
        oracle = str(raw.get("oracle") or "")
        outcome = str(raw.get("outcome") or "undetermined")
        route_raw = raw.get("route")
        route: Mapping[str, Any] = route_raw if isinstance(route_raw, Mapping) else {}
        evidence_raw = raw.get("evidence")
        evidence: Mapping[str, Any] = (
            evidence_raw if isinstance(evidence_raw, Mapping) else {}
        )
        rows.append(
            Row(
                finding_id=str(raw.get("id") or ("F-" + str(i).zfill(3))),
                title=str(raw.get("title") or oracle),
                severity=(str(raw.get("severity")) if outcome == "reported" else None),
                oracle_id=oracle,
                asset_id=str(raw.get("asset") or asset),
                confidence=float(raw.get("confidence") or 0.0),
                route_strength=str(route.get("strength") or route.get("source") or "contract"),
                route_ref=(str(route["ref"]) if route.get("ref") else None),
                replay_status=str(evidence.get("replay_status") or "NEVER"),
                outcome=outcome,
                reason_code=str(raw.get("reason_code") or ""),
                hits=_HITS.get(outcome),
                capsule_id=(str(evidence["capsule_id"]) if evidence.get("capsule_id") else None),
            )
        )
    engagement = {
        "id": "ENG-ARTIFACT",
        "asset": asset,
        "scope": "只读验证（来自运行产物）",
        "confirmed": sum(1 for r in rows if r.outcome == "reported"),
        "total": len(rows),
    }
    return ConsoleState(findings=rows, engagement=engagement)



def _evidence_index(record: Mapping[str, Any]) -> dict[tuple[str, str], str]:
    """hunt.json 的逐面证据：``(oracle, route_ref) → capsule_id``（B155）。

    **不能只按 oracle 建索引**：同一个 oracle 的多个面各有一份 capsule，
    按 oracle 存最后一份会把"面 A 的发现"指到"面 B 的证据"——B148 的老坑。
    """
    index: dict[tuple[str, str], str] = {}
    raw = record.get("evidence")
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        return index
    for item in raw:
        if not isinstance(item, Mapping):
            continue
        oracle = str(item.get("oracle") or "")
        ref = str(item.get("route_ref") or "")
        cid = str(item.get("capsule_id") or "")
        if oracle and cid:
            index[(oracle, ref)] = cid
    return index


def _from_faces_run(record: Mapping[str, Any], faces: Sequence[Any]) -> ConsoleState:
    """用 `faces_run`（逐面权威记录）建行。

    hunt 的 `findings` 只是 `{oracle: verdict}`，没有面；逐面事实在 `faces_run` 里，
    **那才是能说清依据的那一层**。证据按面索引（B154 之后 hunt 真的封 capsule）。
    """
    asset = str(record.get("target_host") or "unknown")
    evidence = _evidence_index(record)
    rows: list[Row] = []
    for raw in faces:
        if not isinstance(raw, Mapping):
            continue
        oracle = str(raw.get("oracle_id") or "")
        if not oracle:
            continue
        verdict = str(raw.get("verdict") or "INCONCLUSIVE")
        route_ref = str(raw.get("route_ref") or "")
        outcome = _OUTCOME.get(verdict.upper(), "undetermined")
        template = str(raw.get("template") or "")
        face_no = raw.get("face_index")
        face_total = raw.get("faces_total")
        title = f"{oracle} {'在 ' + template if outcome == 'reported' and template else '未确认'}"
        if face_no and face_total:
            title += f"（面 {face_no}/{face_total}）"
        rows.append(
            Row(
                finding_id=f"F-{oracle}-{_ref_tail(route_ref)}",
                title=title,
                severity=SEVERITY.get(oracle, "medium") if outcome == "reported" else None,
                oracle_id=oracle,
                asset_id=asset,
                confidence=1.0 if outcome == "reported" else 0.0,
                route_strength=str(raw.get("strength") or "observed"),
                route_ref=route_ref or None,
                replay_status="NEVER",
                outcome=outcome,
                reason_code=str(raw.get("reason") or verdict.upper()),
                hits=_HITS[outcome],
                # 有证据就显示真的那份；没有才是 None（不编，也不瞒）
                capsule_id=evidence.get((oracle, route_ref)) or None,
            )
        )
    verified = sum(1 for r in rows if r.outcome == "reported")
    engagement = {
        "id": str(record.get("engagement_id") or "ENG-ARTIFACT"),
        "asset": asset,
        "scope": "只读验证（来自运行产物 · 逐面）",
        "confirmed": verified,
        "total": len(rows),
        "decision": record.get("decision"),
        "reason": record.get("reason"),
        "bindings": record.get("bindings"),
    }
    return ConsoleState(findings=rows, engagement=engagement)


def load_record(record: Mapping[str, Any]) -> ConsoleState:
    """把一轮运行的 JSON 记录映射成控制台状态（纯函数，无 IO）。"""
    state = _load_record(record)
    # 模型自述（B161）：hunt.json 的 `agent_claim`。**只展示**，不进 verdicts/覆盖。
    claim = record.get("agent_claim")
    if isinstance(claim, Mapping):
        state.agent_claim = {
            "summary": str(claim.get("summary") or ""),
            "unresolved": [str(x) for x in (claim.get("unresolved") or [])],
        }
    return state


def _load_record(record: Mapping[str, Any]) -> ConsoleState:
    asset = str(record.get("target_host") or "unknown")
    rows: list[Row] = []
    seq = 0

    confirmed = record.get("findings") or {}
    if isinstance(confirmed, Sequence) and not isinstance(confirmed, (str, bytes)):
        return _from_report_findings(record, confirmed)

    # hunt 的逐面记录：比 `{oracle: verdict}` 更能说清依据（B145）
    faces = record.get("faces_run") or []
    if isinstance(faces, Sequence) and not isinstance(faces, (str, bytes)) and faces:
        return _from_faces_run(record, faces)
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


#: 覆盖账本的文件名（B87 产出，和 hunt.json 同目录）。
LEDGER_NAME = "coverage.json"


def _load_ledger(path: Path) -> dict[str, Any]:
    """读覆盖账本。**读不到就空**——控制台不替运行产物编数字。"""
    if not path.is_file():
        return {}
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return dict(doc) if isinstance(doc, Mapping) else {}


#: 目录里按这个顺序找主产物：hunt 最全 → run 的 findings → 只有账本。
ARTIFACT_NAMES = ("hunt.json", "findings.json", "coverage.json")


def _resolve_artifact(path: Path) -> Path:
    """给目录就按候选顺序找主产物；给文件就原样用。"""
    if not path.is_dir():
        return path
    for name in ARTIFACT_NAMES:
        candidate = path / name
        if candidate.is_file():
            return candidate
    return path / ARTIFACT_NAMES[0]


def _platform_assets(path: Path) -> list[Path]:
    """平台产物目录的形状：`<root>/asset-N/findings.json`（B167）。

    只认"子目录里有 findings.json"，不认名字——`asset-1` 是 cmd_platform 的约定，
    但控制台不该因为有人改了目录名就整页空白。
    """
    if not path.is_dir():
        return []
    found = [
        d for d in path.iterdir()
        if d.is_dir() and (d / "findings.json").is_file()
    ]

    def order(d: Path) -> tuple[int, int | str]:
        match = re.fullmatch(r"asset-(\d+)", d.name)
        return (0, int(match.group(1))) if match else (1, d.name)

    return sorted(found, key=order)


def _merge_ledgers(items: Sequence[tuple[str, Mapping[str, Any]]]) -> dict[str, Any]:
    """把各资产的覆盖账本合成一份**平台合计**（B168）。

    口径写在 `scope` 里：面/适用对/执行/缺口**全部相加**，缺口保留资产标签，
    完整性取"所有资产都完整"。这样控制台说的数字与平台报告里的
    `覆盖合计：已判定 X/Y 面` 是同一口径，不会一个说合计、一个说单资产。
    """
    summary: dict[str, Any] = {
        "faces": 0, "faces_covered": 0, "oracles_selected": 0,
        "applicable_pairs": 0, "executed": 0, "gaps": 0,
    }
    outcomes: dict[str, int] = {}
    gaps: list[dict[str, Any]] = []
    caveats: list[str] = []
    complete = True
    for name, ledger in items:
        single = ledger.get("summary") or {}
        for key in summary:
            if key == "outcomes":
                continue
            summary[key] += int(single.get(key) or 0)
        for key, value in (single.get("outcomes") or {}).items():
            outcomes[str(key)] = outcomes.get(str(key), 0) + int(value or 0)
        for gap in ledger.get("gaps") or ():
            if isinstance(gap, Mapping):
                gaps.append({**gap, "asset": name})
        comp = ledger.get("completeness") or {}
        if comp.get("complete") is False:
            complete = False
        for caveat in comp.get("caveats") or ():
            caveats.append(f"{name}：{caveat}")
    summary["outcomes"] = outcomes
    return {
        "scope": "platform",
        "summary": summary,
        "completeness": {"complete": complete, "caveats": caveats[:8]},
        "gaps": gaps[:50],
    }


def load_platform(path: Path) -> ConsoleState:
    """把一次 `veripen platform` 的多个资产合成一个控制台状态（B167）。

    控制台原来只能打开单资产目录：把 `out/platform` 指给它，页面是空的——
    而平台恰恰是"多资产"那个出口。这里逐资产复用 `load_file`
    （账本、证据路径、自述都按各自的目录算），再给行加资产前缀：

    * `finding_id` 前缀 `asset-N/`：否则两个资产撞 id，详情页会串；
    * `asset_id` 换成目录名：结论表里才看得出是哪台。
    """
    assets = _platform_assets(path)
    rows: list[Row] = []
    ledgers: list[tuple[str, Mapping[str, Any]]] = []
    for sub in assets:
        state = load_file(sub)
        ledger = state.coverage.get("ledger")
        if isinstance(ledger, Mapping) and ledger:
            ledgers.append((sub.name, ledger))
        for row in state.findings:
            rows.append(
                replace(row, finding_id=f"{sub.name}/{row.finding_id}", asset_id=sub.name)
            )
    merged_ledger = _merge_ledgers(ledgers) if ledgers else {}
    confirmed = sum(1 for r in rows if r.outcome == "reported")
    return ConsoleState(
        findings=tuple(rows),
        coverage={"ledger": merged_ledger} if merged_ledger else {},
        engagement={
            "id": "platform",
            "asset": f"{len(assets)} 个资产",
            "scope": "平台汇总（逐资产产物的合并视图；口径与数字以各资产自己的报告为准）",
            "confirmed": confirmed,
            "total": len(rows),
            "decision": None,
            "reason": None,
            "bindings": None,
        },
    )


def load_file(path: str | Path) -> ConsoleState:
    """读产物文件**或目录**；不存在/空/坏 JSON 都退化成空状态。

    目录支持是必须的：用户敲 ``--hunt out/run-xxx`` 时给的就是目录。
    同时尝试同目录的 ``coverage.json``：账本是独立产物，缺它不影响结论页，
    但覆盖页会少掉"哪条面根本没判定"那一段。
    """
    raw = Path(path)
    p = _resolve_artifact(raw)
    if not p.is_file():
        # 单资产目录里没有顶层产物时，可能是平台目录（`asset-N/findings.json`）
        assets = _platform_assets(raw)
        if assets:
            return load_platform(raw)
        return ConsoleState()
    try:
        record = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return ConsoleState()
    if not isinstance(record, Mapping):
        return ConsoleState()
    state = load_record(record)
    _attach_capsule_paths(state, p.parent)
    ledger = _load_ledger(p.parent / LEDGER_NAME)
    if ledger:
        state.coverage = {**state.coverage, "ledger": ledger}
        # 只有账本的产物没有 target_host：资产名从账本里取，别显示 unknown
        if str(state.engagement.get("asset") or "") in ("", "unknown"):
            asset = str(ledger.get("asset_id") or "")
            if asset:
                state.engagement = {**state.engagement, "asset": asset}
    return state


def _attach_capsule_paths(state: ConsoleState, root: Path) -> None:
    """把每行的证据文件路径补上（B157）。

    控制台原来只显示 `capsule_id`，复现命令写成 `make replay ID=<finding_id>`——
    而 `make replay` 要的是 **capsule 文件路径**，所以那条命令照抄必然报"载入失败"。
    文件名来自内容寻址（`<self_sha256>.json`），所以这里只做**查找**，不做推断：
    找不到就是 None，界面如实说"盘上没有"。
    """
    base = root / "capsules"
    if not base.is_dir():
        return
    by_stem = {p.stem: p for p in base.rglob("*.json")}
    if not by_stem:
        return
    rows = []
    for row in state.findings:
        stem = row.capsule_id.split(":")[-1] if row.capsule_id else ""
        found = by_stem.get(stem)
        # 正斜杠：命令要能跨 shell 粘（与报告里的路径口径一致，B153）
        rows.append(replace(row, capsule_path=found.as_posix()) if found else row)
    state.findings = tuple(rows)


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
