"""B68 · `veripen platform`：企业级编排（`H13 B68`）。

企业级不是"功能更多"，是**能被别人接手**：一次运行跑多个资产，
报告要能让没参与的人看懂"打了什么、为什么停、谁批的、证据在哪"。

四件事：

1. **多资产**：逐资产跑同一条 `run` 链路，各自落独立证据目录；
   路由来源二选一：`--openapi <契约>` 或 `--discover`（无契约，现场常这样，B162）；
2. **审批阻断**：`--require-approval` 时动作停在 `PENDING`，落 `approvals` 与 `task_events`；
   `--approve` 才是人工放行（模拟 operator 签字），否则退出码 3；
3. **汇总报告**：逐资产结论 + 总计 + 审计节（`B53` 五表，空表也显式）；
   报告里写明**这一批的路由是契约给的还是探测来的**——两者证据强度不同；
4. **Playbook**：命中率写进报告，没命中也要写原因。

用法：`veripen platform --targets a,b --openapi spec.json`
　　　`veripen platform --targets a,b --discover`

**不在这里做的事**：不判断结论对不对（oracle 的事），不重新封存证据（run 的事）。
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from veripen_core.blackboard import EventLog  # noqa: E402
from veripen_core.guard.approval import (  # noqa: E402
    ApprovalGate,
    ApprovalRequired,
    ApprovalTTLExpired,
)
from veripen_core.store import Store  # noqa: E402

from ._discover_args import add_discovery_args  # noqa: E402
from ._io import setup_stdout  # noqa: E402

SEP = chr(0x2F)
TASK_ID = "platform-run"


def _events_as_dicts(store: Store, task_id: str) -> list[dict[str, Any]]:
    return [
        {
            "seq": e.seq, "kind": e.kind, "ok": e.ok, "tool": e.tool,
            "oracle_id": e.oracle_id, "route_ref": e.route_ref,
            "elapsed_ms": e.elapsed_ms, "detail": dict(e.detail or {}),
        }
        for e in EventLog(store).for_task(task_id)
    ]


def _approve_or_block(store: Store, *, approve: bool, now: int) -> int:
    """判据 B：审批阻断。返回 0 表示放行，3 表示停在 PENDING。"""
    gate = ApprovalGate.from_spec(
        [{"action_class": "scope_expand"}],
        store=store, events=EventLog(store), task_id=TASK_ID,
    )
    try:
        gate.require("scope_expand", now=now, impact={"assets": "multi", "mode": "platform"})
    except ApprovalRequired:
        if not approve:
            print("审批：PENDING —— 批量动作需人工放行（--approve 模拟签字）")
            return 3
        try:
            gate.decide("scope_expand", approved=True, by="operator",
                        lease_owner="agent", now=now + 1, reason="批量验证放行")
        except (PermissionError, ApprovalTTLExpired, KeyError) as exc:
            print(f"审批失败：{type(exc).__name__}: {exc}", file=sys.stderr)
            return 3
        print("审批：APPROVED（operator 签字）")
        return 0
    return 0


def _playbook_line() -> str:
    """Playbook 命中：跑一遍加载与校验，把结论写进报告（没命中要说原因）。

    ``validate_against_oracles`` 对未知 oracle **抛异常**而不是返回问题列表，
    所以加载与校验必须一起包住——报告里的这一行不许因为校验失败而炸掉整份报告。
    """
    try:
        from veripen_core.playbooks import load_dir, validate_against_oracles
        from veripen_oracles import ALL_ORACLES

        books = load_dir()
        if not books:
            return "| playbook | 目录为空（仓内无 playbooks/*.yaml） |"
        # 第二参是 ``{oracle_id: impact}`` 映射，**不是类列表**——
        # 传错会在"未知 oracle"上假红，那是调用方的问题、不是 playbook 的问题。
        table = {
            str(getattr(o.meta, "id", "")): str(getattr(getattr(o.meta, "impact", None), "value",
                                                        getattr(o.meta, "impact", "")))
            for o in ALL_ORACLES
        }
        validate_against_oracles(books, table)
        ok, problems = True, ()
    except Exception as exc:  # noqa: BLE001 —— 校验失败要写成"不可用"，不是崩掉
        return f"| playbook | 校验未过：{type(exc).__name__}: {str(exc)[:80]} |"
    status = "可用" if ok else f"校验未过：{'; '.join(list(problems)[:2])}"
    return f"| playbook | {len(books)} 个，{status} |"


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(prog="veripen platform", description="企业级编排")
    ap.add_argument("--targets", default="", help="逗号分隔的多个目标（与 --profile 二选一）")
    ap.add_argument("--profile", default="", help="客户档路径（与 --targets 二选一）")
    ap.add_argument("--openapi", default="", help="各目标共用的 OpenAPI 文档路径（与 --discover 二选一）")
    ap.add_argument("--out", default=str(ROOT / "out" / "platform"))
    ap.add_argument("--db", default=str(ROOT / "out" / "platform.db"))
    ap.add_argument("--require-approval", action="store_true", help="批量动作需人工放行")
    ap.add_argument("--approve", action="store_true", help="模拟 operator 立即签字")
    ap.add_argument("--exclude", default="", help="透传给单资产链路的 oracle 黑名单")
    # 无契约的资产（B162）：参数只在 `_discover_args` 定义一次，透传给每个资产
    add_discovery_args(ap)
    args = ap.parse_args(sys.argv[1:] if argv is None else argv)
    setup_stdout()

    # ── 资产来源：客户档 or 直给（二选一，不静默取一个）──────────
    profile = None
    profile_hash = ""
    if args.profile and args.targets:
        print("--profile 与 --targets 不能同时给（档里已有 assets）", file=sys.stderr)
        return 2
    if not args.profile and not args.targets:
        print("必须给 --profile 或 --targets", file=sys.stderr)
        return 2

    if args.profile:
        import os

        from veripen_core.profile import ProfileError, load_profile
        from veripen_oracles import ALL_ORACLES

        try:
            profile = load_profile(args.profile)
        except Exception as exc:  # noqa: BLE001 —— 档坏了要如实说，不猜
            print(f"profile 解析失败：{type(exc).__name__}: {exc}", file=sys.stderr)
            return 2

        resolved = {
            a.id: os.environ.get(profile.asset_env.get(a.id, ""), "")
            for a in profile.assets
        }
        try:
            profile.assert_resolvable(resolved)     # fail-closed：缺一个就整批拒跑
        except ProfileError as exc:
            names = ", ".join(sorted(profile.asset_env.values()))
            print(f"{exc}（需要注入：{names}）", file=sys.stderr)
            return 2

        targets = [resolved[a.id] for a in profile.assets]
        profile_hash = profile.profile_hash(resolved)

        # 档身份要打在终端上，不只是写进报告：操作员得知道自己按哪个档在跑
        print(f"客户档：{profile.name} · {profile.customer_id} · {profile.engagement_id}")
        print(f"档策略：可用 oracle {len(profile.allowed_oracles())} 类 / "
              f"需审批 {len(profile.oracles.needs_approval)} 类")

        # 白名单翻黑名单：cmd_run 只有 --exclude，本批不改它的契约
        allowed = set(profile.allowed_oracles())
        deny = sorted(o.meta.id for o in ALL_ORACLES if o.meta.id not in allowed)
        extra = [x.strip() for x in args.exclude.split(",") if x.strip()]
        args.exclude = ",".join(sorted(set(deny) | set(extra)))

        # 档说"要批"就必须批：命令行只决定谁签了字，不决定要不要签
        if profile.oracles.needs_approval and not args.require_approval:
            args.require_approval = True
            print(f"审批：档要求 {len(profile.oracles.needs_approval)} 类动作需放行"
                  f"（{', '.join(profile.oracles.needs_approval)}）")
    else:
        targets = [t.strip() for t in args.targets.split(",") if t.strip()]

    if not targets:
        print("没有目标", file=sys.stderr)
        return 2
    # 路由来源二选一（B162）：企业现场经常没有契约，`run` 早就支持探测，平台没跟上。
    if args.discover and args.openapi:
        print("--openapi 与 --discover 二选一：有契约就别猜面", file=sys.stderr)
        return 2
    if not args.discover and not args.openapi:
        print("需要 --openapi <契约> 或 --discover（不替你猜）", file=sys.stderr)
        return 2
    spec_path: Path | None = None
    if not args.discover:
        spec_path = Path(args.openapi)
        if not spec_path.exists():
            print(f"契约不存在：{spec_path}", file=sys.stderr)
            return 2

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    db = Path(args.db)
    db.parent.mkdir(parents=True, exist_ok=True)
    store = Store(str(db)).init()

    started = time.time()
    if args.require_approval:
        rc = _approve_or_block(store, approve=args.approve, now=int(started))
        if rc != 0:
            (out / "platform-report.md").write_text(
                "# 平台运行报告\n\n| 项 | 值 |\n|---|---|\n"
                "| 结论 | **审批 PENDING，未执行** |\n"
                f"| 资产数 | {len(targets)} |\n"
                "| 审计 | 见 task_events（含 approval:request） |\n",
                encoding="utf-8",
            )
            return rc

    from veripen_cli import cmd_run

    per_asset: list[dict[str, Any]] = []
    for i, target in enumerate(targets, start=1):
        sub = out / f"asset-{i}"
        print(f"[{i}/{len(targets)}] {target}")
        extra_args: list[str] = []
        if args.exclude:
            extra_args += ["--exclude", args.exclude]
        if profile is not None:
            # 档的 selectors 就是 operator 的确认（`19 §3.1` 的出口）：
            # 只采纳这些标签，不是"全部结构推断"。
            tags = ",".join(sorted({s.semantic_tag for s in profile.selectors}))
            if tags:
                extra_args += ["--confirm-tags", tags]
        route_args: list[str]
        if args.discover:
            route_args = [
                "--discover",
                "--discover-rounds", str(args.discover_rounds),
                "--discover-pages", str(args.discover_pages),
            ]
            if args.render:
                route_args.append("--render")
            if args.submit_forms:
                route_args.append("--submit-forms")
        else:
            route_args = ["--openapi", str(spec_path)]
        rc = cmd_run.main(["--target", target, *route_args, "--out", str(sub), *extra_args])
        found: list[dict[str, Any]] = []
        findings_file = sub / "findings.json"
        # **rc != 0 就不读 findings.json**（B159）：目录里躺着的是上一轮的产物，
        # 这一轮什么都没发生。照旧读会把"重跑被拒"渲染成跟成功那次一模一样的数字
        # ——rc 那一列说 2，结论列说"已验证 2"，自相矛盾。
        if rc == 0 and findings_file.exists():
            data = json.loads(findings_file.read_text(encoding="utf-8"))
            found = list(data.get("findings", []))
        elif rc != 0:
            print(f"  资产 {i} 退出码 {rc}：本轮没有产出，不采用 {findings_file.name} 里的旧结论",
                  file=sys.stderr)
        # 证据份数按**结论引用的** capsule 算（B153）：证据目录里可能躺着上一轮的文件
        # （每轮一个 run 子目录），数文件会把两轮算成一份报告的证据。
        cap_ids = {
            str((f.get("evidence") or {}).get("capsule_id"))
            for f in found
            if (f.get("evidence") or {}).get("capsule_id")
        }
        per_asset.append({
            "target": target, "rc": rc, "dir": sub.name,
            "findings": found, "capsules": len(cap_ids),
            "produced": rc == 0,
        })

    # ── 汇总报告 ────────────────────────────────────────────────
    from veripen.report.audit import AuditInput, render_audit_section

    total = sum(len(a["findings"]) for a in per_asset)
    verified = sum(
        len([f for f in a["findings"] if f.get("outcome") == "reported"]) for a in per_asset
    )
    events = _events_as_dicts(store, TASK_ID)
    audit_text = render_audit_section(AuditInput(task_id=TASK_ID, events=events))

    lines = [
        "# 平台运行报告",
        "",
        f"- 生成时间：{time.strftime('%Y-%m-%dT%H:%M:%S%z')}",
    ]
    if profile is not None:
        # 档的身份必须写进报告：接手的人要知道"这批是按哪个客户档跑的"
        lines += [
            f"- 客户档：`{profile.name}` · {profile.customer_id} · "
            f"engagement `{profile.engagement_id}` · hash `{profile_hash[:12]}`",
            f"- 档策略：可用 oracle {len(profile.allowed_oracles())} 类 / "
            f"需审批 {len(profile.oracles.needs_approval)} 类",
        ]
    lines += [
        f"- 资产数：{len(per_asset)}"
        + (
            f"（本轮未产出 {sum(1 for a in per_asset if not a['produced'])} 个）"
            if any(not a["produced"] for a in per_asset) else ""
        ),
        f"- 结论：已验证 **{verified}** / 总项 {total}",
        # 路由来源必须写进报告（B162）：契约面与探测面的证据强度不同，读者要知道。
        (
            f"- 路由来源：主动探测（无契约，{args.discover_rounds} 轮 / 每轮 {args.discover_pages} 页）"
            if args.discover else f"- 路由来源：契约 `{args.openapi}`"
        ),
        "",
        "## 1. 逐资产结论",
        "",
        "| # | 目标 | 退出码 | 结论项 | 已验证 | capsule | 证据目录 |",
        "|---|---|---|---|---|---|---|",
    ]
    for i, a in enumerate(per_asset, start=1):
        if not a["produced"]:
            # 未产出的资产不许把数字填上：rc 说失败、结论列说"已验证 2"是自相矛盾（B159）
            lines.append(f"| {i} | `{a['target']}` | {a['rc']} | — | — | — | `{a['dir']}` |")
            continue
        v = len([f for f in a["findings"] if f.get("outcome") == "reported"])
        lines.append(
            f"| {i} | `{a['target']}` | {a['rc']} | {len(a['findings'])} | {v} | "
            f"{a['capsules']} | `{a['dir']}` |"
        )
    lines += ["", "## 2. 资产面明细", ""]
    for i, a in enumerate(per_asset, start=1):
        lines.append(f"### 资产 {i} · `{a['target']}`")
        lines.append("")
        if not a["findings"]:
            if not a["produced"]:
                lines += [f"（本轮未产出：退出码 {a['rc']}；目录里的旧产物未被采用）", ""]
            else:
                lines += ["（无结论项：该资产未跑出结果）", ""]
            continue
        lines += ["| 项 | 结论 | 原因 | 证据 |", "|---|---|---|---|"]
        for f in a["findings"]:
            lines.append(
                f"| {f.get('ruleId') or f.get('id')} | {f.get('outcome')} | "
                f"{f.get('reason_code') or '-'} | "
                f"{(f.get('evidence') or {}).get('capsule_id') or '-'} |"
            )
        lines.append("")

    lines += [
        "## 3. 运行面",
        "",
        "| 项 | 值 |",
        "|---|---|",
        f"| 审批 | {'已签（operator）' if args.require_approval and args.approve else ('PENDING' if args.require_approval else '未启用')} |",
        f"| 事件数 | {len(events)} |",
        _playbook_line(),
        f"| 耗时 | {time.time() - started:.1f}s |",
        "",
        "## 4. 智能体行为审计",
        "",
        audit_text,
    ]
    report = out / "platform-report.md"
    report.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print("-" * 62)
    print(f"汇总：{len(per_asset)} 资产 / 已验证 {verified} / 总项 {total}")
    print(f"报告：{report}")
    bad = [a for a in per_asset if a["rc"] != 0]
    if bad:
        print(f"有 {len(bad)} 个资产退出码非 0（它们的旧产物未被采用，报告里标 —）",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
