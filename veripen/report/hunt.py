"""`hunt` 的可交付报告（B166）。

`run` 一直有 `report.md`，`hunt` 只有 `hunt.json` + 终端输出——客户拿到的是一个
要人肉翻的 JSON。这个模块把**同一份 record** 渲染成 markdown：

    record = {...}                      # cmd_hunt 写进 hunt.json 的那个 dict
    (out / "hunt.md").write_text(render_hunt_markdown(record, capsules_dir=...))

同源是关键：报告里的每一个数都来自 `hunt.json`，不另算一遍——
两份出口对不上时，只可能是渲染错，不可能是"口径不同"。

三条口径照抄 `report.md`：
1. **只报有证据的结论**：VERIFIED 必须有 capsule；没有就在证据列写 `-` 并标注；
2. **未确认必须带原因**：只写"未确认"等于不说为什么（D1）；
3. **自述不计入判定**：模型在 `finish` 里说的话单独一段，不进结论表。
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence


def _ref_tail(ref: str) -> str:
    """路由引用只给尾段（不透明哈希），与报告/控制台同口径。"""
    text = str(ref or "")
    return text.rsplit("/", 1)[-1][:8] if "/" in text else text[:8]


def _cap_path(capsules_dir: str, capsule_id: str) -> str:
    """证据文件路径（内容寻址）：`<dir>/<self_sha256>.json`，正斜杠便于跨 shell 粘贴。"""
    sha = str(capsule_id or "").split(":")[-1]
    base = str(capsules_dir or "").replace("\\", "/").rstrip("/")
    return f"{base}/{sha}.json" if base else f"{sha}.json"


def render_hunt_markdown(
    record: Mapping[str, Any],
    *,
    capsules_dir: str,
    generated_at: str,
) -> str:
    host = str(record.get("target_host") or "unknown")
    source = str(record.get("source") or "contract")
    decision = str(record.get("decision") or "")
    reason = str(record.get("reason") or "")
    findings = record.get("findings") or {}
    not_confirmed = record.get("not_confirmed") or {}
    faces: Sequence[Mapping[str, Any]] = [
        f for f in (record.get("faces_run") or []) if isinstance(f, Mapping)
    ]
    coverage = record.get("coverage") or {}
    summary = coverage.get("summary") or {}
    evidence = [e for e in (record.get("evidence") or []) if isinstance(e, Mapping)]
    capsules = [str(c) for c in (record.get("capsules") or [])]
    claim = record.get("agent_claim") or {}

    confirmed = [f for f in faces if str(f.get("verdict")) == "VERIFIED"]
    unconfirmed = [f for f in faces if str(f.get("verdict")) != "VERIFIED"]

    lines: list[str] = [
        f"# 渗透验证报告 · {host}",
        "",
        f"- 生成时间：{generated_at}",
        "- 命令：`veripen hunt`（agent 逐步推进，逐面判定）",
        f"- 路由来源：{'主动探测（无契约）' if source == 'discover' else '契约'}",
        f"- 决策：{decision}（{reason}）",
        "",
        "## 1. 结论",
        "",
    ]

    if confirmed:
        lines += [
            "| oracle | 面 | 路由（尾 8 位） | 结论 | 证据 |",
            "|---|---|---|---|---|",
        ]
        by_face = {(str(e.get("oracle")), str(e.get("route_ref"))): e for e in evidence}
        for f in confirmed:
            oracle = str(f.get("oracle_id") or "")
            ref = str(f.get("route_ref") or "")
            cap = str((by_face.get((oracle, ref)) or {}).get("capsule_id") or "")
            lines.append(
                f"| `{oracle}` | {f.get('face_index', 1)}/{f.get('faces_total', 1)} | "
                f"`{_ref_tail(ref)}` | VERIFIED | {cap or '-'} |"
            )
    else:
        lines.append("（本轮没有确认的结论）")

    lines += ["", "### 未确认（带原因）", ""]
    if unconfirmed:
        lines += ["| oracle | 面 | 路由（尾 8 位） | 结论 | 原因 |", "|---|---|---|---|---|"]
        for f in unconfirmed:
            lines.append(
                f"| `{f.get('oracle_id')}` | {f.get('face_index', 1)}/{f.get('faces_total', 1)} | "
                f"`{_ref_tail(str(f.get('route_ref') or ''))}` | {f.get('verdict')} | "
                f"{f.get('reason') or '-'} |"
            )
    else:
        lines.append("（无）")

    # 机器口径的粗视图（按 oracle 汇总）与逐面表并列，两者不一致时要能看出来
    if not_confirmed:
        lines += ["", "按 oracle 汇总（`hunt.json.not_confirmed`）：", ""]
        for oracle in sorted(not_confirmed):
            entry = not_confirmed[oracle] or {}
            lines.append(
                f"- `{oracle}`：{entry.get('verdict') or '-'}——{entry.get('reason') or '-'}"
            )
    lines.append("")

    lines += [
        "## 2. 覆盖",
        "",
        f"- 面：{int(summary.get('faces_covered') or 0)}/{int(summary.get('faces') or 0)} 已判定"
        f" · 适用 {int(summary.get('applicable_pairs') or 0)} 对"
        f" · 执行 {int(summary.get('executed') or 0)}"
        f" · 缺口 {int(summary.get('gaps') or 0)}",
        f"- 完整：{'是' if coverage.get('complete') else '否'}",
        "",
    ]
    if coverage.get("complete") is False:
        # "完整：否" 必须带原因：缺口为 0 也可能是"提前停"，读者否则会以为还有没跑的面
        lines.append(
            f"> **覆盖不完整**：本轮以 `{decision}`（{reason}）结束，"
            "覆盖只反映停下之前做完的部分——不要把\"没写\"读成\"没问题\"。"
        )
        for caveat in (coverage.get("caveats") or [])[:3]:
            lines.append(">")
            lines.append("> " + str(caveat).replace("**", ""))
        lines.append("")

    lines += ["## 3. 证据", ""]
    if capsules:
        lines += [
            f"- 链长 {len(capsules)}，链头 `{capsules[-1][:12]}…`（内容寻址，逐环前向链接）",
            "",
            "| capsule | oracle | 面 | 离线重判 |",
            "|---|---|---|---|",
        ]
        for e in evidence:
            cap = str(e.get("capsule_id") or "")
            if not cap:
                continue
            oracle = str(e.get("oracle") or "")
            ref = str(e.get("route_ref") or "")
            cmd = f"`veripen replay --capsule {_cap_path(capsules_dir, cap)} --mode offline`"
            lines.append(f"| {cap} | `{oracle}` | `{_ref_tail(ref)}` | {cmd} |")
        lines += [
            "",
            f"证据目录：`{str(capsules_dir).replace(chr(92), '/')}`",
        ]
    else:
        lines += ["（本轮没有落任何 capsule：上面每一条结论都不可重放）"]

    claim_summary = str(claim.get("summary") or "")
    claim_unresolved = [str(x) for x in (claim.get("unresolved") or [])]
    lines += ["", "## 4. 模型自述（不计入判定）", ""]
    if claim_summary or claim_unresolved:
        if claim_summary:
            lines += ["> " + claim_summary.replace("\n", " "), ""]
        if claim_unresolved:
            lines.append("自述未解决：" + "、".join(claim_unresolved))
    else:
        lines.append("（本次没有自述：scripted 决策器不调用 finish）")

    lines += [
        "",
        "## 5. 附录：口径",
        "",
        "- `VERIFIED` 表示判定成立且落了证据；**没有证据的结论不会出现在上表**。",
        "- `--mode offline` 只重判、不重发请求；它证明\"这条结论能从证据里推出来\"，"
        "不证明目标此刻仍然如此。",
        "- 未确认的三种情形分开写：`REFUTED`（测了，不成立）、`BLOCKED`（缺输入）、"
        "`INCONCLUSIVE`（判不了）——不要把它们读成同一件事。",
        "- 复现命令里的证据路径**相对报告所在目录**：换台机器、换个目录也读得出来"
        "（绝对路径会把操作员的机器布局写进交付物）。",
        "- 模型自述是**它自己说的话**，不计入判定、不计入覆盖。",
        "",
    ]
    return "\n".join(lines)
