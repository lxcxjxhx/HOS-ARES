"""B68 · `hos-ares platform`：企业级编排（`H13 B68`）。

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

用法：`hos-ares platform --targets a,b --openapi spec.json`
　　　`hos-ares platform --targets a,b --discover`

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

from hos_ares.report.glossary import explain_with_code
from hos_ares_core.blackboard import EventLog  # noqa: E402
from hos_ares_core.guard.approval import (  # noqa: E402
    ApprovalGate,
    ApprovalRequired,
    ApprovalTTLExpired,
)
from hos_ares_core.store import Store  # noqa: E402

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


def _portable_path(raw: str) -> str:
    """交付物里的路径要可移植（B247）。

    能相对到当前目录就给相对路径；不在当前目录下就只留文件名——
    绝对路径会把操作员本机的目录结构带进交付物，别人照抄也跑不了。
    """
    if not raw:
        return "（未指定）"
    p = Path(raw)
    try:
        return p.resolve().relative_to(Path.cwd().resolve()).as_posix()
    except (ValueError, OSError):
        return p.name


def _report_line(profile: Any) -> str:
    """报告口径如实写（B197）：框架与分发级别要么来自档，要么是默认值。"""
    if profile is None:
        return "- 报告口径：未指定档（默认分发 internal、不出合规映射）"
    fw = ", ".join(sorted(profile.frameworks)) or "未指定"
    return f"- 报告口径：分发 {profile.distribution} · 框架 {fw}"


def _rate_line(profile_qps: float, effective_qps: float, concurrency: int) -> str:
    """限速如实写（B186）：档里写着 qps，就得说清这一轮到底按多少跑。"""
    if not effective_qps:
        return "- 限速：未指定（按 run 默认 1 qps 执行）"
    if profile_qps and abs(effective_qps - profile_qps) < 1e-9:
        base = f"{effective_qps:g} qps（档 policy.qps）"
    elif profile_qps:
        base = f"{effective_qps:g} qps（命令行覆盖档里的 {profile_qps:g}）"
    else:
        base = f"{effective_qps:g} qps（命令行）"
    if concurrency > 1:
        base += f"；并发：档要求 {concurrency} —— 平台按资产串行执行，该值未使用"
    return f"- 限速：{base}"


def _credential_line(
    token_by_asset: dict[str, str], shared: str, assets: int,
) -> str:
    """凭据怎么给的——三种情形分开写，都不落原文（B185）。"""
    if token_by_asset:
        return (f"- 凭据：按资产注入 {len(token_by_asset)} 项"
                f"（{', '.join(sorted(token_by_asset))}；不落原文）")
    if shared and assets > 1:
        return (f"- 凭据：**同一 token 注入 {assets} 个资产**"
                "（已显式 --allow-shared-token；跨资产复用凭据）")
    if shared:
        return "- 凭据：主身份 token 已注入（不落原文）"
    return "- 凭据：未提供——auth 相关 oracle 会落 BLOCKED"


def _approve_or_block(store: Store, *, approve: bool, now: int, task_id: str,
                      by: str = "operator") -> int:
    """判据 B：审批阻断。返回 0 表示放行，3 表示停在 PENDING。

    `by` 是**签字人**（B174）：聊天入口代签时必须写 chat，不能一律记 operator——
    否则审计里"谁批的"这一栏是编的。

    `task_id` 必须是**这一轮**的 id（B371）：`--db` 默认落在 `out/platform.db` 上且
    跨轮复用，若仍用固定 id，审计节会把历史批次的审批账当成这一轮的。
    """
    gate = ApprovalGate.from_spec(
        [{"action_class": "scope_expand"}],
        store=store, events=EventLog(store), task_id=task_id,
    )
    try:
        gate.require("scope_expand", now=now, impact={"assets": "multi", "mode": "platform"})
    except ApprovalRequired:
        if not approve:
            print("审批：PENDING —— 批量动作需人工放行（--approve 模拟签字）")
            return 3
        try:
            gate.decide("scope_expand", approved=True, by=by,
                        lease_owner="agent", now=now + 1, reason="批量验证放行")
        except (PermissionError, ApprovalTTLExpired, KeyError) as exc:
            print(f"审批失败：{type(exc).__name__}: {exc}", file=sys.stderr)
            return 3
        print(f"审批：APPROVED（{by} 签字）")
        return 0
    return 0


def _playbook_line() -> str:
    """Playbook 命中：跑一遍加载与校验，把结论写进报告（没命中要说原因）。

    ``validate_against_oracles`` 对未知 oracle **抛异常**而不是返回问题列表，
    所以加载与校验必须一起包住——报告里的这一行不许因为校验失败而炸掉整份报告。
    """
    try:
        from hos_ares_core.playbooks import load_dir, validate_against_oracles
        from hos_ares_oracles import ALL_ORACLES

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

    ap = argparse.ArgumentParser(prog="hos-ares platform", description="企业级编排")
    ap.add_argument("--targets", default="", help="逗号分隔的多个目标（与 --profile 二选一）")
    ap.add_argument("--profile", default="", help="客户档路径（与 --targets 二选一）")
    ap.add_argument("--openapi", default="", help="各目标共用的 OpenAPI 文档路径（与 --discover 二选一）")
    ap.add_argument("--out", default=str(ROOT / "out" / "platform"))
    ap.add_argument("--db", default=str(ROOT / "out" / "platform.db"))
    ap.add_argument("--require-approval", action="store_true", help="批量动作需人工放行")
    ap.add_argument("--approve", action="store_true", help="模拟 operator 立即签字")
    ap.add_argument(
        "--approve-by", default="operator",
        help="签字人（聊天入口代签时传 chat；审计里\"谁批的\"这一栏据实写）",
    )
    ap.add_argument("--exclude", default="", help="透传给单资产链路的 oracle 黑名单")
    # 与 run / hunt 同一口径（B353）：平台是最危险的一条（多资产），
    # 却原来只能靠客户档写 deny——不带档就没有任何黑名单入口。
    ap.add_argument("--deny", default="",
                    help="逗号分隔：资产 id / 主机名 / CIDR（如 DENY_SET_DEFAULT），透传给每个资产")
    ap.add_argument(
        "--token", default="",
        help="主身份 token（**单资产**用；多资产默认拒绝透传，见 --token-for）",
    )
    ap.add_argument(
        "--token-for", action="append", default=[], metavar="资产=token",
        help="按资产给凭据（可重复）：多资产时唯一被接受的给法",
    )
    ap.add_argument(
        "--allow-shared-token", action="store_true",
        help="显式确认：多个资产共用同一个 token（跨资产复用凭据，风险自负）",
    )
    ap.add_argument(
        "--qps", type=float, default=0.0,
        help="出网限速（请求/秒）。0 = 用档的 policy.qps；档没写就用 run 的默认 1.0",
    )
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

        from hos_ares_core.profile import ProfileError, load_profile
        from hos_ares_oracles import ALL_ORACLES

        try:
            profile = load_profile(args.profile)
        except FileNotFoundError:
            # 档不存在是最常见的输入错（B343）：别把 Python 异常 repr 倒给客户
            print(f"客户档不存在：{args.profile}", file=sys.stderr)
            return 2
        except ProfileError as exc:
            print(f"客户档不合法：{exc}", file=sys.stderr)
            return 2
        except Exception as exc:  # noqa: BLE001 —— 其余异常如实说，不猜
            print(f"客户档解析失败：{type(exc).__name__}: {exc}", file=sys.stderr)
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

        asset_ids = [a.id for a in profile.assets]
        targets = [resolved[a.id] for a in profile.assets]
        profile_hash = profile.profile_hash(resolved)

        # 启动即校验（02 §6 第 8 步）——B259 之前**只有 profile_diff 脚本在调**，
        # 产品路径没调：未知 oracle / 未知标签 / 未接线键在平台上都是静默的。
        from hos_ares_contract import TAG_VOCAB
        from hos_ares_core.profile import validate_profile

        issues = validate_profile(
            profile,
            known_oracles={c.meta.id for c in ALL_ORACLES},
            known_tags=TAG_VOCAB,
            resolved=resolved,
        )
        fails = [i for i in issues if i.level == "fail"]
        if fails:
            for issue in fails:
                print(f"档校验失败 {issue.code}：{issue.detail}", file=sys.stderr)
            return 2
        for issue in issues:
            # 未接线的键也要说：客户写了 `scope.max_redirects: 0` 会以为已经限住
            print(f"注意：{issue.code} —— {issue.detail}")

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
        asset_ids = [f"asset-{i}" for i in range(1, len(targets) + 1)]

    if not targets:
        print("没有目标", file=sys.stderr)
        return 2

    # ── 凭据：多资产默认不许一个 token 打通（B185）──────────────────
    # 一个 token 发给三个资产 = 把 A 的凭据递给 B 和 C。企业现场每个资产一份凭据，
    # 这里默认拒绝透传，要复用必须显式 --allow-shared-token。
    token_by_asset: dict[str, str] = {}
    for item in args.token_for:
        name, sep, value = item.partition("=")
        name, value = name.strip(), value.strip()
        if not sep or not name or not value:
            print(f"--token-for 要写成 资产=token：{item!r}", file=sys.stderr)
            return 2
        if name not in asset_ids:
            print(f"--token-for 里的资产未知：{name!r}（可选：{', '.join(asset_ids)}）",
                  file=sys.stderr)
            return 2
        token_by_asset[name] = value
    if args.token and args.token_for:
        print("--token 与 --token-for 二选一：前者是单资产快捷方式，后者按资产给",
              file=sys.stderr)
        return 2
    shared_token = args.token
    if shared_token and len(targets) > 1 and not args.allow_shared_token:
        print(
            f"拒绝把同一个 token 发给 {len(targets)} 个资产（跨资产复用凭据）。\n"
            "  按资产给：--token-for " + " --token-for ".join(f"{a}=<token>" for a in asset_ids)
            + "\n  确实共用同一凭据再加 --allow-shared-token",
            file=sys.stderr,
        )
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

    # 档的 scope.deny（B187）：原来只写进档的哈希，没有任何代码把它交给闸门——
    # 也就是"配了黑名单"，但闸门的 deny 永远是空集。现在按资产映射成主机传下去。
    deny_hosts: list[str] = [x.strip() for x in args.deny.split(",") if x.strip()]
    if profile is not None and profile.deny:
        # deny 里既可以写资产 id，也可以直接写主机名：两种都收
        def _host_of(url: str) -> str:
            from urllib.parse import urlsplit

            return urlsplit(url).hostname or ""

        profile_asset_ids = {a.id for a in profile.assets}
        mapped = {
            _host_of(str(resolved.get(a.id, ""))) for a in profile.assets
            if a.id in set(profile.deny)
        } - {""}
        # 资产 id 映射成主机；不是资产的条目（主机名）原样留着
        deny_hosts = sorted(set(deny_hosts) | mapped | (set(profile.deny) - profile_asset_ids))
    if deny_hosts:
        print(f"黑名单：{', '.join(deny_hosts)} —— 命中的资产整面 BLOCKED（不发包）")

    # 限速：命令行 --qps 优先，其次档里的 policy.qps，最后留 0（= run 的默认 1.0，不改口径）
    profile_qps = float(profile.qps) if profile is not None else 0.0
    effective_qps = args.qps if args.qps > 0 else profile_qps
    if profile_qps and args.qps > 0 and abs(args.qps - profile_qps) > 1e-9:
        print(f"限速：命令行 {args.qps:g} qps 覆盖档里的 {profile_qps:g} qps")

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    db = Path(args.db)
    db.parent.mkdir(parents=True, exist_ok=True)
    store = Store(str(db)).init()

    started = time.time()
    # 每一轮一个 task_id（B371）：`--db` 默认是 `out/platform.db`，**跨轮复用**；
    # 固定 id 会让审计节把历史上所有批次的审批/动作账都抱进这一份交付物
    # （实测：没开 --require-approval 的一轮里出现 64 条 approval、12 条 APPROVED）。
    task_id = f"{TASK_ID}-{int(started * 1000)}"
    if args.require_approval:
        rc = _approve_or_block(store, approve=args.approve, now=int(started),
                               task_id=task_id, by=args.approve_by)
        if rc != 0:
            (out / "platform-report.md").write_text(
                "# 平台运行报告\n\n| 项 | 值 |\n|---|---|\n"
                "| 结论 | **审批 PENDING，未执行** |\n"
                f"| 资产数 | {len(targets)} |\n"
                f"| 审计 | 见 task_events（task_id=`{task_id}`，含 approval:request） |\n",
                encoding="utf-8",
            )
            return rc

    from hos_ares_cli import cmd_run

    per_asset: list[dict[str, Any]] = []
    for i, target in enumerate(targets, start=1):
        sub = out / f"asset-{i}"
        print(f"[{i}/{len(targets)}] {target}")
        extra_args: list[str] = []
        if args.exclude:
            extra_args += ["--exclude", args.exclude]
        if effective_qps > 0:
            extra_args += ["--qps", f"{effective_qps:g}"]
        if deny_hosts:
            extra_args += ["--deny", ",".join(deny_hosts)]
        if profile is not None:
            # 档里写的报告偏好（B197）：原来一条都不往下传，于是报告永远用默认值
            if profile.frameworks:
                extra_args += ["--frameworks", ",".join(sorted(profile.frameworks))]
            if profile.distribution and profile.distribution != "internal":
                extra_args += ["--distribution", profile.distribution]
        asset_token = token_by_asset.get(asset_ids[i - 1], "") or shared_token
        if asset_token:
            # 平台原来不传 token：带鉴权的资产在平台里永远只能落 BLOCKED（B163）
            extra_args += ["--token", asset_token]
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
        findings_file = sub / "findings.json"
        cov_file = sub / "coverage.json"

        def _mtime(path: Path) -> float | None:
            return path.stat().st_mtime if path.exists() else None

        # 「这一轮有没有写产物」用**写之前/写之后的 mtime 差**判断（B363）：
        # 之前用 `mtime >= t0 - 1`，1 秒容差太宽——重跑被拒（rc=2，什么都没写）时，
        # 上一轮几秒前写的 findings.json 仍落在容差里，于是旧结论被当成新的采纳。
        before_findings = _mtime(findings_file)
        before_cov = _mtime(cov_file)
        rc = cmd_run.main(["--target", target, *route_args, "--out", str(sub), *extra_args])
        fresh = _mtime(findings_file) not in (None, before_findings)
        found: list[dict[str, Any]] = []
        if fresh:
            data = json.loads(findings_file.read_text(encoding="utf-8"))
            found = list(data.get("findings", []))
        elif findings_file.exists():
            print(f"  资产 {i} 退出码 {rc}：本轮没有产出，不采用 {findings_file.name} 里的旧结论",
                  file=sys.stderr)
        # 覆盖账本（B164）：单资产报告有"覆盖"一节，平台汇总里原来没有——
        # 于是"2 条结论"看上去像"只测了 2 项"，读者看不到分母。
        coverage: dict[str, Any] | None = None
        if _mtime(cov_file) not in (None, before_cov):
            try:
                loaded = json.loads(cov_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                loaded = None
            if isinstance(loaded, dict):
                coverage = loaded
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
            # `produced` 的语义是"这一轮写没写产物"，不是"跑得成不成"（B355）：
            # 全被挡下时 rc=1 但产物齐全，写"本轮未产出"会让报告自相矛盾。
            "produced": fresh, "coverage": coverage,
        })

    # ── 汇总报告 ────────────────────────────────────────────────
    from hos_ares.report.audit import AuditInput, render_audit_section

    total = sum(len(a["findings"]) for a in per_asset)
    not_run = sum(
        1 for a in per_asset for f in a["findings"] if f.get("outcome") == "not_run"
    )
    verified = sum(
        len([f for f in a["findings"] if f.get("outcome") == "reported"]) for a in per_asset
    )
    faces_total = faces_covered = gaps_total = 0
    gaps_critical = gaps_warn = 0
    for a in per_asset:
        s = (a.get("coverage") or {}).get("summary") or {}
        faces_total += int(s.get("faces") or 0)
        faces_covered += int(s.get("faces_covered") or 0)
        gaps_total += int(s.get("gaps") or 0)
        sev = s.get("gaps_by_severity") or {}
        gaps_critical += int(sev.get("critical") or 0)
        gaps_warn += int(sev.get("warn") or 0)
    events = _events_as_dicts(store, task_id)
    audit_text = render_audit_section(AuditInput(task_id=task_id, events=events))

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
    # 黑名单也要写进报告（B328/B355）：终端打了、报告没写，接手的人就不知道
    # "这批资产里哪些是被边界挡下的"。放在档块**外面**——命令行 `--deny` 也要算。
    if deny_hosts:
        lines.append(
            f"- 黑名单：`{'`、`'.join(sorted(deny_hosts))}` —— "
            "命中的资产整面 BLOCKED（不发包）")
    lines += [
        f"- 资产数：{len(per_asset)}"
        + (
            f"（本轮未产出 {sum(1 for a in per_asset if not a['produced'])} 个）"
            if any(not a["produced"] for a in per_asset) else ""
        ),
        f"- 结论：已验证 **{verified}** / 总项 {total}"
        # 总项 0 必须解释（B417 / B353）：全被挡下时它确实是 0，但和"已验证 0"
        # 并排出现会被读成"测了 0 项都没问题"。事实是**一项都没进入判定**。
        + (
            "（没有任何判定进入执行：资产被边界/输入挡下，或契约没匹配到面——"
            "这不是「没有漏洞」，是「没有测」）"
            if total == 0 else ""
        ),
        # 「0 验证」必须解释（B356）：读者最容易把它读成"没有漏洞"，
        # 而全被挡下时它其实意味着"一项都没测"。
        *(
            [f"- 说明：{not_run} 项全部未执行（被边界/输入挡下，请求没发出去）——"
             "这不是「没有漏洞」，是「没有测」；解除黑名单或补齐输入后重跑"]
            if verified == 0 and not_run and not_run >= total else []
        ),
        # 覆盖率必须带分母（D3）：只写"2 条结论"会被读成"只测了 2 项"（B164）
        (
            f"- 覆盖合计：已判定 **{faces_covered}**/{faces_total} 面 · "
            f"缺口 {gaps_total}（critical {gaps_critical} / warn {gaps_warn}）"
            if faces_total else "- 覆盖合计：—（本轮没有覆盖账本）"
        ),
        # 路由来源必须写进报告（B162）：契约面与探测面的证据强度不同，读者要知道。
        # 路径要**可移植**（B247）：旧版原样打出 `--openapi` 参数，于是交付物里出现
        # `C:\\1AAA-PROJECT\\…\\spec.json` 这种本机绝对路径——带出了操作员的目录结构，
        # 别人拿到报告也照抄不了。
        (
            f"- 路由来源：主动探测（无契约，{args.discover_rounds} 轮 / 每轮 {args.discover_pages} 页）"
            if args.discover else f"- 路由来源：契约 `{_portable_path(args.openapi)}`"
        ),
        # 凭据只记"有没有 / 怎么给的"，**不落原文**：报告是要发给客户的（B163/B185）
        _credential_line(token_by_asset, shared_token, len(targets)),
        _report_line(profile),
        _rate_line(profile_qps, effective_qps,
                   int(profile.concurrency) if profile is not None else 1),
        "",
        "## 1. 逐资产结论",
        "",
        "| # | 目标 | 退出码 | 结论项 | 已验证 | capsule | 覆盖（面） | 缺口 | 证据目录 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for i, a in enumerate(per_asset, start=1):
        if not a["produced"]:
            # 未产出的资产不许把数字填上：rc 说失败、结论列说"已验证 2"是自相矛盾（B159）
            lines.append(
                f"| {i} | `{a['target']}` | {a['rc']} | — | — | — | — | — | `{a['dir']}` |"
            )
            continue
        v = len([f for f in a["findings"] if f.get("outcome") == "reported"])
        summary = (a.get("coverage") or {}).get("summary") or {}
        cov_cell = (
            f"{int(summary.get('faces_covered') or 0)}/{int(summary.get('faces') or 0)}"
            if summary else "—"
        )
        gap_cell = str(int(summary.get("gaps") or 0)) if summary else "—"
        lines.append(
            f"| {i} | `{a['target']}` | {a['rc']} | {len(a['findings'])} | {v} | "
            f"{a['capsules']} | {cov_cell} | {gap_cell} | `{a['dir']}` |"
        )
    # B355：capsule 列 0 必须有一句解释——它要么是「没有结论需要取证」，要么是
    # 「全被挡下」。读者只看到 0 会以为证据丢了。
    if any(a["produced"] and a["capsules"] == 0 for a in per_asset):
        lines += ["", "> capsule 为 0 的资产：这一轮没有一条结论需要取证（通常是被边界或输入挡下），"
                      "不是证据丢了。"]
    # B247：章节号从 §1 起，而单资产 report.md 从 §0 起（那里的 0 是摘要）——
    # 两报告并排对照的人会绕，这里把基准写出来，代价是一行字。
    lines += ["", "> 本文章节从 §1 起；单资产 `report.md` 从 §0 起（§0 是摘要），"
                  "跨报告对照时注意编号基准不同。"]
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
            # 原因列要有人话（B255）：hunt 与 run 报告在 B248 已改，平台这张表当时漏了——
            # 同一份交付物里，一边写"探针标记没有出现"，一边写 `marker_not_fired`。
            reason = explain_with_code(str(f.get("reason_code") or "-"))
            # 「已判否」不许印成 not_run（B371）：这一列是给客户读的"结论"，
            # 把"跑过、无该问题"写成"未执行"是反的。平台靠 findings.json 里的
            # `refuted` 位区分——那一位是 B371 同一批加进 JSON 的。
            outcome = str(f.get("outcome") or "-")
            if outcome == "not_run" and f.get("refuted"):
                outcome = "已判否（跑过、无该问题）"
            lines.append(
                f"| {f.get('ruleId') or f.get('id')} | {outcome} | "
                f"{reason} | "
                f"{(f.get('evidence') or {}).get('capsule_id') or '-'} |"
            )
        lines.append("")
        summary = (a.get("coverage") or {}).get("summary") or {}
        if summary:
            sev = summary.get("gaps_by_severity") or {}
            comp = (a.get("coverage") or {}).get("completeness") or {}
            lines.append(
                f"覆盖：{int(summary.get('faces_covered') or 0)}/{int(summary.get('faces') or 0)} 面 · "
                f"适用 {int(summary.get('applicable_pairs') or 0)} 对 · "
                f"执行 {int(summary.get('executed') or 0)} · "
                f"缺口 {int(summary.get('gaps') or 0)}"
                f"（critical {int(sev.get('critical') or 0)} / warn {int(sev.get('warn') or 0)}） · "
                f"完整：{'是' if comp.get('complete') else '否'}"
            )
            for caveat in (comp.get("caveats") or [])[:3]:
                lines.append(f"  - {str(caveat).replace('**', '')}")
        else:
            lines.append("覆盖：缺 coverage.json（该资产这一轮没有账本）")
        lines.append("")

    # 能力边界（B397）：与 run/hunt 的报告同一份实现、同一段文案。
    # 平台汇总里先写一次，管理层读"能测什么"时不必逐份翻资产报告。
    from hos_ares.report.limits import capability_limits, limits_block  # noqa: PLC0415

    lines += limits_block(capability_limits())
    lines += [
        "## 3. 运行面",
        "",
        "| 项 | 值 |",
        "|---|---|",
        f"| 审批 | {'已签（' + args.approve_by + '）' if args.require_approval and args.approve else ('PENDING' if args.require_approval else '未启用')} |",
        f"| 事件数 | {len(events)} |",
        _playbook_line(),
        f"| 耗时 | {time.time() - started:.1f}s |",
        "",
        "## 4. 智能体行为审计",
        "",
        audit_text,
    ]
    report = out / "platform-report.md"
    body = "\n".join(lines) + "\n"
    report.write_text(body, encoding="utf-8", newline=chr(10))
    # 汇总报告也要能证明"没被手改"（B256）：各资产早就有 report.manifest.json，
    # 而交给管理层的那一份反而没有。
    # 形状与 `hos_ares.report.lint.build_manifest` **对齐**（B257）：`version` + `sections`
    # + `report_sha256`，只是 sections 里只有 markdown——平台汇总没有三视图，
    # 别假装它有；这样同一个校验器能读两种清单，不用写两套。
    import hashlib as _hashlib

    md_sha = _hashlib.sha256(body.encode("utf-8")).hexdigest()
    manifest = {
        "version": 1,
        "sections": {"markdown": md_sha},
        # 逐文件哈希（B317）：与 run 那一路同形，客户可直接 `sha256sum` 核。
        "files": {"platform-report.md": md_sha},
        "report_sha256": md_sha,
        "assets": len(per_asset),
        "generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    # 清单自哈希（B321）：与 run/hunt 同形
    from hos_ares.report.lint import dumps_artifact as _dumps

    manifest["manifest_sha256"] = _hashlib.sha256(
        _dumps(manifest).encode("utf-8")).hexdigest()
    (out / "platform-report.manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print("-" * 62)
    print(f"汇总：{len(per_asset)} 资产 / 已验证 {verified} / 总项 {total}")
    print(f"报告：{report}（+ platform-report.manifest.json）")
    bad = [a for a in per_asset if a["rc"] != 0]
    if bad:
        print(f"有 {len(bad)} 个资产退出码非 0（它们的旧产物未被采用，报告里标 —）",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
