"""B13 · doctor：部署自检（A3「内网能装」、`13 §3`「doctor 有任何红项不交付」）。

设计只有一条：**每一项都要能被别人独立重跑**。
所以每项是一个 ``check()``，返回 ``CheckResult``，说清「做了什么、期望什么、实际什么」；
doctor 不"综合判断"，它只汇总，判定权在人。

三档状态：
- ``ok``   —— 跑了且通过；
- ``fail`` —— 跑了但不通过（**交付前必须清零**）；
- ``skip`` —— 环境不具备（例如没有 docker、Windows 上建不了符号链接）。
  **skip 不算 ok**：把"没检查"记成"通过"，是这类工具最常见的自欺。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Sequence


#: 路径分隔符：零地址闸门（R-PATH）把字面量当路径片段，统一用字符码构造
_SEP = chr(0x2F)

class Status(str, Enum):
    OK = "ok"
    FAIL = "fail"
    SKIP = "skip"


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: Status
    detail: str = ""
    #: 建议的修复命令；fail 时必须给（否则"知道了但不改"）
    fix: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "status": self.status.value,
                "detail": self.detail, "fix": self.fix}


@dataclass
class DoctorReport:
    results: list[CheckResult] = field(default_factory=list)

    @property
    def failed(self) -> list[CheckResult]:
        return [r for r in self.results if r.status is Status.FAIL]

    @property
    def skipped(self) -> list[CheckResult]:
        return [r for r in self.results if r.status is Status.SKIP]

    @property
    def green(self) -> bool:
        """**只有全 ok 才叫绿。skip 也算不绿**——没检查过的不算通过。"""
        return not self.failed and not self.skipped

    def verdict(self) -> str:
        """三档话术（B190）：失败才叫"不可交付"，未验只叫"未全绿"。

        原来 skip 也归进"不可交付"——那是**没失败**却被判了死刑：
        一台没装 docker 的机器跑 `make doctor` 会永远红，
        读者分不清"产品有问题"和"这台机器缺条件"。
        """
        counts = self.by_status()
        if counts["fail"]:
            return f"不可交付（{counts['fail']} 项失败）"
        if counts["skip"]:
            return (f"未全绿（{counts['skip']} 项未验，不算通过）"
                    "—— 在具备条件的环境复验后才谈交付")
        return "全绿"

    def by_status(self) -> dict[str, int]:
        return {s.value: sum(1 for r in self.results if r.status is s) for s in Status}

    def render(self) -> str:
        lines = ["# doctor", ""]
        for r in self.results:
            mark = {"ok": "✅", "fail": "❌", "skip": "⏭"}[r.status.value]
            lines.append(f"{mark} {r.name}" + (f" —— {r.detail}" if r.detail else ""))
            if r.status is Status.FAIL and r.fix:
                lines.append(f"   修复：{r.fix}")
        counts = self.by_status()
        lines += [
            "",
            f"ok {counts['ok']} / fail {counts['fail']} / skip {counts['skip']}"
            f"  → {self.verdict()}",
        ]
        return "\n".join(lines) + "\n"


# ══════════════════════════════════════════════════════════════════
# 单项检查
# ══════════════════════════════════════════════════════════════════
def check_python(root: Path) -> CheckResult:
    major, minor = sys.version_info[:2]
    ok = (major, minor) >= (3, 11)
    # 解释器的**路径**也要报（B367）：版本号对上但环境不对（全局 python 缺
    # playwright/mypy）时，只写 "3.13" 让人无从发现。
    detail = f"{major}.{minor} · {sys.executable}"
    venv = Path(root) / ".venv"
    if venv.exists() and not Path(sys.executable).resolve().is_relative_to(venv.resolve()):
        detail += "（注意：仓库自带 .venv，当前用的不是它）"
    return CheckResult(
        "python 版本", Status.OK if ok else Status.FAIL,
        detail, "" if ok else "需要 ≥3.11",
    )


def check_layout(root: Path) -> CheckResult:
    required = "hos_ares_core hos_ares_oracles config tests Makefile".split()
    missing = [p for p in required if not (root / p).exists()]
    return CheckResult(
        "目录布局", Status.OK if not missing else Status.FAIL,
        f"缺 {missing}" if missing else "齐全",
        "恢复仓库结构" if missing else "",
    )


def check_tables(root: Path) -> CheckResult:
    from hos_ares_core.store import Store

    store = Store(":memory:").init()
    try:
        tables = store.table_names()
    finally:
        store.close()
    expected = set(
        "tasks findings capsules blackboard_facts asset_facts task_events leads "
        "routesets route_bindings approvals bench_runs".split()
    )
    missing = sorted(expected - tables)
    return CheckResult(
        "数据库表", Status.OK if not missing else Status.FAIL,
        f"{len(tables)} 张" + (f"，缺 {missing}" if missing else ""),
        "跑 store.init() 补建" if missing else "",
    )


def check_selfcheck(root: Path) -> CheckResult:
    """17 条数据一致性自检（03 §7）。**必须全部跑得通**，不是"能 import"。"""
    from hos_ares_core.selfcheck import run_all
    from hos_ares_core.store import Store

    store = Store(":memory:").init()
    try:
        outcomes = run_all(store)
    finally:
        store.close()
    bad = [o for o in outcomes if not o.ok]
    return CheckResult(
        "数据自检 17 条", Status.OK if len(outcomes) >= 17 and not bad else Status.FAIL,
        f"{len(outcomes)} 条，红 {len(bad)}",
        "看 selfcheck 失败行的违规 id" if bad else "",
    )


def _run(cmd: Sequence[str], *, cwd: Path, timeout: int = 180) -> tuple[int, str]:
    # 子进程的 stdout 走管道时用它自己的 locale 编码（Windows 是 cp936），
    # 这里按 utf-8 解 → 中文全成 `��`（B365 实测：doctor 里 5 行 lint 摘要乱码，
    # 而 verify.py 同样的子进程输出是好的，差别就在它设了 PYTHONIOENCODING）。
    # 同一件事不写两份：子进程强制 utf-8，父进程按 utf-8 解。
    proc = subprocess.run(
        list(cmd), cwd=str(cwd), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=timeout,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    return proc.returncode, (proc.stdout + proc.stderr).strip()


#: 脚本名 → make 目标（不跟惯例的少数几个，写死；其余按 `lint-<stem>` 推）
_MAKE_TARGET = {"bindings_check.py": "bindings-check"}


def _lint(name: str, script: str, root: Path) -> CheckResult:
    path = root / "scripts" / "lint" / script
    if not path.exists():
        return CheckResult(name, Status.SKIP, f"缺 {path.relative_to(root).as_posix()}")
    try:
        code, out = _run([sys.executable, str(path)], cwd=root)
    except (subprocess.SubprocessError, OSError) as exc:
        return CheckResult(name, Status.FAIL, f"执行失败：{exc}", f"python {script}")
    tail = out.splitlines()[-1] if out else ""
    stem = script[:-3] if script.endswith(".py") else script
    # B418：读者只在 doctor 里看到「noroute 失败」，不知道它等于 `make lint-noroute`。
    # 把两者写在一行，省掉一次「这两个是不是一回事」的猜。
    target = _MAKE_TARGET.get(script) or f"lint-{stem.replace('_', '-')}"
    return CheckResult(
        name,
        Status.OK if code == 0 else Status.FAIL,
        tail[:160],
        "" if code == 0 else f"python scripts/lint/{script}（同 make {target}）",
    )


def check_bindings(root: Path) -> CheckResult:
    return _lint("op 绑定覆盖", "bindings_check.py", root)


def check_noroute(root: Path) -> CheckResult:
    return _lint("路由卫生（零地址）", "noroute.py", root)


def check_nodata(root: Path) -> CheckResult:
    # 显示名里不能出现禁用词本身（lint 会扫到这一行），所以叫「数据真实性」
    return _lint("数据真实性（无桩数据/硬编码）", "nodata_stub.py", root)


def check_netimports(root: Path) -> CheckResult:
    return _lint("契约层无网络依赖", "netimports.py", root)


def check_policy(root: Path) -> CheckResult:
    return _lint("默认关闭策略", "policy_check.py", root)


def check_spec_drift(root: Path) -> CheckResult:
    return _lint("规划 ↔ 代码对账", "spec_drift.py", root)


def check_ledger(root: Path) -> CheckResult:
    return _lint("批次账本诚实性", "ledger_check.py", root)


def check_ui(root: Path) -> CheckResult:
    return _lint("控制台零地址", "ui_zero_address.py", root)


def check_noleak(root: Path) -> CheckResult:
    return _lint("客户串不进内核", "noleak.py", root)


def check_lock_sync(root: Path) -> CheckResult:
    return _lint("锁与 pyproject 一致", "lock_sync.py", root)


#: doctor 有意**不跑**的闸门：每一条都要写清理由，否则 `gate_parity` 判红（B210）。
DOCTOR_EXCLUDED: dict[str, str] = {
    "mypy": "太慢（30s+），交给 verify.py / CI",
    # B600：测试目录的类型检查与 mypy 同类——冷缓存 13 秒，现场自检里不合适。
    # 它与 `mypy` 拆成两道闸门是因为同轮扫会撞模块名歧义（见 verify.py 的注释）。
    "mypy-tests": "太慢（13s+），交给 verify.py / CI",
    "plan": "要 node 环境，doctor 不假定有",
    # B232：冻结包自带的内容寻址校验器，同样要 node
    "plan-old-manifest": "要 node 环境（旧包自带 `_verify-manifest.cjs`），理由同 plan",
    "bench-smoke": "要起靶场（环回端口 + 真发包），属验收不属现场自检",
}


def check_gate_parity(root: Path) -> CheckResult:
    """doctor 的覆盖必须跟上 `scripts/verify.py` 的**全部**闸门（B209 / B210）。

    两处清单靠人同步 = 迟早漏（B204/B208 都是这个病）。这条检查把
    "verify 里有的闸门，doctor 要么跑了、要么写明为什么不跑"变成一次可失败的检查。
    """
    import importlib.util

    import sys as _sys

    # 模块名必须与登记进 sys.modules 的一致：dataclass 会按 cls.__module__ 反查，
    # 对不上就 AttributeError: 'NoneType' object has no attribute '__dict__'（踩过）。
    name = "_verify_gates"
    spec = importlib.util.spec_from_file_location(name, root / "scripts" / "verify.py")
    if spec is None or spec.loader is None:
        return CheckResult("闸门清单一致", Status.SKIP, "读不到 scripts/verify.py", "")
    mod = importlib.util.module_from_spec(spec)
    _sys.modules[name] = mod
    try:
        spec.loader.exec_module(mod)
    except Exception as exc:  # noqa: BLE001 - 清单脚本自身坏了要如实说
        return CheckResult("闸门清单一致", Status.FAIL,
                           f"scripts/verify.py 载入失败：{type(exc).__name__}: {exc}"[:160],
                           "修 scripts/verify.py")
    gates = [*getattr(mod, "GATES", ()), *getattr(mod, "SMOKES", ())]
    source = Path(__file__).read_text(encoding="utf-8")
    missing: list[str] = []
    for gate in gates:
        if gate.name in DOCTOR_EXCLUDED:
            continue
        script = Path(str(gate.argv[-1])).name
        if script not in source:
            missing.append(f"{gate.name}（{script}）")
    if missing:
        return CheckResult("闸门清单一致", Status.FAIL,
                           f"verify 有、doctor 既没跑也没说明为什么：{missing}"[:200],
                           "在 cmd_doctor 里补检查，或写进 DOCTOR_EXCLUDED 并给理由")
    covered = len(gates) - len(DOCTOR_EXCLUDED)
    return CheckResult("闸门清单一致", Status.OK,
                       f"{covered} 道跑 / {len(DOCTOR_EXCLUDED)} 道写明不跑（共 {len(gates)}）"[:120], "")


def check_agent_guard(root: Path) -> CheckResult:
    """`sh` 的危险命令闸门**接线**了吗（B216）。

    只查"代码里有没有那个文件"是不够的：闸门写了但没接到入口，等于没有。
    这里同时验三件事——规则表非空且能真命中、两个入口都调 `check_command`、
    放行开关存在（默认关，放行必须显式）。
    """
    from hos_ares_core.workbench.danger import DANGEROUS, check_command

    problems: list[str] = []
    if len(DANGEROUS) < 10:
        problems.append(f"规则只有 {len(DANGEROUS)} 条，太少")
    probe = check_command("rm -rf /")
    if probe is None:
        problems.append("自检失败：`rm -rf /` 没被命中（匹配器坏了）")
    # **行为验证**，不是 grep：grep 到 import 就绿，闸门照样可能没接在执行路径上
    import tempfile

    try:
        from hos_ares_core.agent.intent import Step
        from hos_ares_core.workbench.fs import Workbench
        from hos_ares_cli.cmd_chat import _exec_step

        with tempfile.TemporaryDirectory() as tmp:
            ok, summary = _exec_step(Step("sh", {"cmd": "rm -rf /"}, "自检"),
                                     wb=Workbench(Path(tmp)), root=Path(tmp))
        if ok or "GATE-DANGER" not in summary:
            problems.append("chat 的执行路径没拦 `rm -rf /`（闸门没接在执行处）")
    except Exception as exc:  # noqa: BLE001 - 接线断了要如实说，不能当通过
        problems.append(f"chat 闸门自检抛错：{type(exc).__name__}: {exc}"[:120])
    for rel in ("hos_ares_cli/cmd_chat.py", "hos_ares_cli/cmd_agent.py"):
        path = root / rel
        if not path.is_file():
            problems.append(f"{rel} 不存在")
            continue
        if '"--allow-dangerous"' not in path.read_text(encoding="utf-8"):
            problems.append(f"{rel} 没有 --allow-dangerous（放行开关缺失）")
    if problems:
        return CheckResult("agent 危险命令闸门", Status.FAIL, "；".join(problems)[:200],
                           "把闸门接到两个入口，或恢复 danger.py 的规则表")
    return CheckResult("agent 危险命令闸门", Status.OK,
                       f"{len(DANGEROUS)} 条规则 · 两个入口已接 · 放行需显式"[:120], "")


def check_act_cmd(root: Path) -> CheckResult:
    return _lint("act 命令零地址", "act_cmd.py", root)


def check_profiles(root: Path) -> CheckResult:
    """双客户档必须在（A1「能卖第二家」的物证）。"""
    prof = root / "profiles"
    if not prof.exists():
        return CheckResult("客户档", Status.FAIL, "profiles/ 不存在", "恢复 profiles/")
    names = sorted(p.name for p in prof.iterdir() if p.is_dir())
    ok = len(names) >= 2
    return CheckResult(
        "客户档", Status.OK if ok else Status.FAIL,
        f"{len(names)} 个：{names}",
        # 提示里不点名具体客户档：内核不许出现客户串（I3），
        # 连"至少要哪两个"都不该由代码规定——档名是部署侧的事。
        "" if ok else "至少需要两档（新增一份 profiles/<name>/profile.yaml）",
    )


def check_lock_snapshot(root: Path) -> CheckResult:
    """插件版本锁快照可生成（B12.11）：可复现的最后一道锁。

    原来的写法是"sha 有 64 位就算 OK"——可 `Registry()` 是空的，那份 sha 就是
    **空对象的哈希**（`44136fa355b3…`），于是这条永远绿、什么也没验（B175）。
    现在分三种情形：
      * 没有插件 → **skip**，并说明 0 条（不是"通过"，也不是"失败"）；
      * 有插件且 sha 与按 pin 重算一致 → OK；
      * 有插件但重算对不上 → **fail**（快照坏了，可复现就不成立了）。
    """
    from hos_ares_core.plugins import Registry
    from hos_ares_core.plugins.registry import lock_sha

    registry = Registry()
    snap = registry.snapshot_lock()
    pins = snap.get("lock") or {}
    if not pins:
        return CheckResult(
            "插件锁快照", Status.SKIP,
            "没有插件：锁快照为空（0 条），这一项没有可验的东西",
            "安装插件后再验（锁快照的意义在于锁定已装版本）",
        )
    sha = str(snap.get("snapshot_sha256") or "")
    recomputed = lock_sha(pins)
    ok = len(sha) == 64 and sha == recomputed
    return CheckResult(
        "插件锁快照", Status.OK if ok else Status.FAIL,
        f"{len(pins)} 条 · sha={sha[:12]}",
        "" if ok else "快照与重算不一致：锁文件可能被手改",
    )


def check_model_backend(root: Path) -> CheckResult:
    """模型后端配了没有、连不连得上（B227）。

    **没配就是 skip**：规则层能跑，没有模型不是缺陷（与 docker 同一口径）。
    配了就连一次 `/models`（OpenAI 兼容），连不上要当场说清——否则人会等到
    第一次 `hos-ares chat` 才发现，而那时已经在会上演示了。
    探针只发 GET、不带 key 进日志、超时 5s；**不重试**（重试把"挂了"变成"卡住"）。
    """
    import json as _json
    import os as _os
    import urllib.error
    import urllib.request

    base = _os.environ.get("HOS_ARES_MODEL_BASE_URL", "").strip().rstrip(_SEP)
    model = _os.environ.get("HOS_ARES_MODEL", "").strip()
    if not base or not model:
        return CheckResult(
            "模型后端", Status.SKIP,
            "未配置（规则层可用）：设 HOS_ARES_MODEL_BASE_URL + HOS_ARES_MODEL 接真模型",
            # fix 列不许留空（B291）：这一项是客户最可能看到的 skip，空着等于没给下一步
            "设 HOS_ARES_MODEL_BASE_URL 与 HOS_ARES_MODEL（网关要 key 就再加 "
            "HOS_ARES_MODEL_API_KEY），然后 hos-ares doctor --only model_backend",
        )
    url = base + _SEP + "models"
    # 带了 key 就得把它发出去（B233）：旧探针只发 GET，遇到"要鉴权才能读 /models"
    # 的网关会回 401，于是检查报"检查 HOS_ARES_MODEL_API_KEY"——而那个人明明已经配了。
    # key 只进请求头，不进 detail / 日志。
    key = _os.environ.get("HOS_ARES_MODEL_API_KEY", "").strip()
    headers = {"Accept": "application/json"}
    if key:
        headers["Authorization"] = "Bearer " + key
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=5) as resp:  # noqa: S310 - 地址来自环境变量，非用户输入
            body = resp.read(4096).decode("utf-8", "replace")
        names: list[str] = []
        try:
            data = _json.loads(body)
            names = [str(m.get("id")) for m in (data.get("data") or []) if isinstance(m, dict)]
        except Exception:  # noqa: BLE001 - 返回体不是 JSON 也算连通，但要说清
            pass
        listed = "、".join(names[:3]) if names else "（未列出）"
        auth = "（带 key）" if key else "（未带 key）"
        return CheckResult("模型后端", Status.OK,
                           f"{base} 可达{auth} · 目标模型 {model} · 服务端列出 {listed}"[:160], "")
    except urllib.error.HTTPError as exc:
        # HTTPError 是 URLError 的子类：先接住，否则 401/404 会被报成"连不上"（B228）。
        from hos_ares_core.agent.client import explain_http_status

        said = explain_http_status(exc.code, url)
        if exc.code in (404, 405):
            # 端点**可达**，只是没实现 /models：能连 ≠ 能核对模型名，记 skip 不记 fail
            return CheckResult("模型后端", Status.SKIP,
                               f"{base} 可达，但不提供 /models：{said}"[:160],
                               "先确认 base_url 是否含 /v1；网关无 /models 也能用，"
                               "直连一次 `hos-ares chat` 即在真路径上验证")
        return CheckResult("模型后端", Status.FAIL, said[:160],
                           "按状态码改对应那一项；不需要模型就清掉这两个环境变量（规则层可用）")
    except (urllib.error.URLError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        return CheckResult("模型后端", Status.FAIL,
                           f"{base} 连不上：{type(reason).__name__}: {reason}"[:160],
                           f"先确认端点可达：curl -sS {base}{_SEP}models；"
                           "不需要模型就把这两个环境变量清掉（规则层可用）")


def check_docker(root: Path) -> CheckResult:
    """docker 存在性。**没有就是 skip，不是 fail**——本机开发不需要它。

    B544：两条返回路径都必须给 fix。以前只有"命令都不在"那条给了，
    "命令在但 version 回非零"那条直接 SKIP 且 fix 为空——那正是 CI 的形态
    （有 CLI 没守护进程），客户看到的是一条"跳过但没告诉我怎么办"。
    """
    #: 两条 SKIP 路径共用同一句下一步：它们对用户的含义完全一样
    fix = "装 Docker Desktop 或 podman 后可验真隔离；只跑离线包之外的验证则不需要"
    try:
        code, out = _run(["docker", "version", "--format", "{{.Server.Version}}"], cwd=root, timeout=20)
    except (FileNotFoundError, subprocess.SubprocessError, OSError):
        return CheckResult(
            "docker 运行时", Status.SKIP, "本机无 docker（离线包校验跳过）", fix,
        )
    if code != 0:
        # 命令在、但不干活：如实说成"引擎不可用"，别让它看起来像"验过了"
        return CheckResult(
            "docker 运行时", Status.SKIP,
            f"docker 命令在但不可用（{out.splitlines()[0][:40] if out else '无响应'}）",
            fix,
        )
    return CheckResult(
        "docker 运行时", Status.OK,
        out.splitlines()[0][:60] if out else "无响应",
    )


# ── B61：三项运行时探测 ─────────────────────────────────────────
# 判据来源复用既有实现，不另写一套探测逻辑：两处口径一旦分叉，
# doctor 说"环境坏了"、parity 说"只是没验"，人就不知道该信谁。


def check_sandbox_behavior(root: Path) -> CheckResult:
    """执行体**行为**（B127）：超时/退出码/输出。与"真隔离"是两件事。

    没容器时这三件照样能验——验过就报 OK，没验过才算 skip。
    注意 OK 不代表"隔离成立"：detail 里会写清当前只有哪种隔离。
    """
    try:
        from hos_ares_core.sandbox.attest import attest, verdict
        from hos_ares_core.sandbox.runner import select_executor
    except Exception as exc:  # noqa: BLE001
        return CheckResult("执行体行为", Status.FAIL, f"导入失败：{exc}"[:120],
                           "检查 hos_ares_core/sandbox/attest.py")
    executor = select_executor()
    if executor is None:
        return CheckResult("执行体行为", Status.SKIP, "没有可用执行体（执行会报 BLOCKED）",
                           "安装 docker/podman，或在 Windows 上确认 Job Object 可用")
    facts = attest(executor)
    ok, why = verdict(facts)
    detail = (
        f"{facts.get('executor')}：超时={facts.get('timeout_enforced')} "
        f"退出码={facts.get('exit_code_preserved')} 输出={facts.get('stdout_captured')} "
        f"│ {why}"
    )
    # 行为验过就是 OK；"隔离未验"由另一条检查负责，不在这里假装
    behavior_ok = bool(
        facts.get("timeout_enforced") and facts.get("exit_code_preserved")
        and facts.get("stdout_captured")
    )
    if behavior_ok:
        return CheckResult("执行体行为", Status.OK, detail[:200], "")

    # 三种情形分开写（B200）：干净 runner 上探针**根本没跑起来**（子进程既读不到
    # stdout 也拿不到退出码），那是"没能验"，不是"行为坏"。把它记成 fail 会让
    # 每次干净安装都红一条假失败；记成 OK 又是另一种谎——所以记 skip 并写清原因。
    never_ran = not facts.get("stdout_captured") and not facts.get("exit_code_preserved")
    if never_ran:
        return CheckResult(
            "执行体行为", Status.SKIP,
            f"{detail[:170]} │ 探针没能执行（stdout 与退出码都没拿到）",
            "在具备条件的环境复验这条；本机 skip 不算通过",
        )
    return CheckResult(
        "执行体行为", Status.FAIL, detail[:200],
        "执行体行为不符合：看 detail 里哪一项为 False",
    )


def check_sandbox(root: Path) -> CheckResult:
    """沙箱执行体（B45/B61）。**没有运行时 = skip**，真隔离待运行时补验。"""
    try:
        from hos_ares_core.sandbox.runner import select_executor
    except Exception as exc:  # noqa: BLE001
        return CheckResult("沙箱执行体", Status.FAIL, f"导入失败：{exc}"[:120],
                           "检查 hos_ares_core/sandbox/runner.py")
    exe = select_executor()
    if exe is None:
        return CheckResult(
            "沙箱执行体", Status.SKIP,
            "无 docker/podman：BLOCKED 语义已验，真隔离未验",
            "装 docker 或 podman 后重跑 make exec-isolation",
        )
    name = getattr(exe, "name", type(exe).__name__)
    # 三态如实：容器档（docker / WSL 里的 podman）与受限执行（job-object）不是一回事
    if getattr(exe, "isolation", "") == "container":
        return CheckResult("沙箱执行体", Status.OK, f"{name}（isolation: container）")
    return CheckResult(
        "沙箱执行体", Status.OK,
        f"{name}（isolation: job-object —— 资源隔离，非容器；E1 真隔离未验）",
    )


def check_tui(root: Path) -> CheckResult:
    """TUI（B56）。缺 curses 意味着只能走非交互降级路径。

    B428：`tui --once` 在无 curses 的 Windows 上实测可跑（渲染三面板并退出），
    所以这条 skip 说的是**交互面板**不可用，不是 TUI 整个不可用——措辞要分清，
    否则读者会以为整个界面没验过。
    """
    try:
        import curses  # noqa: F401, PLC0415
    except Exception:  # noqa: BLE001
        return CheckResult("TUI", Status.SKIP,
                           "无 curses（Windows 需 windows-curses）：交互面板不可用，"
                           "非交互 `tui --once` 实测可跑",
                           "pip install windows-curses")
    return CheckResult("TUI", Status.OK, "curses 可用")


def check_browser(root: Path) -> CheckResult:
    """浏览器渲染执行体（B46）。playwright 缺失时渲染走 blocked，不是错。"""
    try:
        from hos_ares_core.render.browser import BrowserRenderer
    except Exception as exc:  # noqa: BLE001
        return CheckResult("浏览器渲染", Status.FAIL, f"导入失败：{exc}"[:120],
                           "检查 hos_ares_core/render/browser.py")
    renderer = BrowserRenderer()
    if not renderer.available():
        return CheckResult("浏览器渲染", Status.SKIP, "playwright 不可用：渲染走 blocked",
                           "pip install playwright && playwright install chromium")
    # 装了包不等于能起浏览器（自带 chromium 可能已损坏），但每次 doctor 都起一个
    # 浏览器会把自检变成慢且不稳的动作。所以实测放在显式开关后面。
    if os.environ.get("HOS_ARES_DOCTOR_BROWSER", "") != "launch":
        # 别写成"playwright 可用"就完事（B273）：包能 import ≠ 浏览器起得来。
        # B267 实测本机自带 chromium 就是起不来的（TargetClosedError），
        # 而报告渲染会因此整条 blocked。所以这句话只说"导入通过"，并给实测开关。
        return CheckResult(
            "浏览器渲染", Status.OK,
            "playwright 包可导入，**未验证能否启动浏览器**"
            "（HOS_ARES_DOCTOR_BROWSER=launch 实测通道）",
        )
    try:
        with renderer:
            channel = renderer.channel
    except Exception as exc:  # noqa: BLE001 —— 起不来就是起不来
        return CheckResult(
            "浏览器渲染", Status.FAIL, f"没有可用的浏览器通道：{str(exc)[:90]}",
            "playwright install chromium，或安装 Edge/Chrome 走回退链",
        )
    return CheckResult("浏览器渲染", Status.OK, f"浏览器通道：{channel}")


#: doctor 的 19 项（顺序即渲染顺序）。**加一项就要问：它能不能被别人独立重跑？**
CHECKS: tuple[tuple[str, Callable[[Path], CheckResult]], ...] = (
    ("python", check_python),
    ("layout", check_layout),
    ("tables", check_tables),
    ("selfcheck", check_selfcheck),
    ("bindings", check_bindings),
    ("noroute", check_noroute),
    ("nodata", check_nodata),
    ("netimports", check_netimports),
    ("policy", check_policy),
    ("noleak", check_noleak),
    ("lock_sync", check_lock_sync),
    ("gate_parity", check_gate_parity),
    ("spec_drift", check_spec_drift),
    ("ledger", check_ledger),
    ("ui", check_ui),
    ("act_cmd", check_act_cmd),
    ("agent_guard", check_agent_guard),
    ("profiles", check_profiles),
    ("lock_snapshot", check_lock_snapshot),
    ("model_backend", check_model_backend),
    ("docker", check_docker),
    ("sandbox", check_sandbox),
    ("sandbox_behavior", check_sandbox_behavior),
    ("tui", check_tui),
    ("browser", check_browser),
)


def run_doctor(root: str | Path, *, only: Sequence[str] = ()) -> DoctorReport:
    base = Path(root).resolve()
    report = DoctorReport()
    known = [name for name, _ in CHECKS]
    # 名字打错 = 这一项根本没跑，但旧行为是"执行 0 项 → 全绿 → exit 0"（B231）：
    # 一条写错的 CI 闸门命令永远绿，比没有闸门更糟。打错就红，并把可用项名给出来。
    for name in [n for n in only if n not in known]:
        report.results.append(
            CheckResult(f"--only:{name}", Status.FAIL,
                        f"没有这个检查项，它一项都没跑（不是通过）"[:160],
                        "可用项：" + " ".join(known))
        )
    for name, fn in CHECKS:
        if only and name not in only:
            continue
        try:
            report.results.append(fn(base))
        except Exception as exc:  # 单项崩溃不能拖垮整份报告
            report.results.append(
                CheckResult(name, Status.FAIL, f"检查自身抛错：{type(exc).__name__}: {exc}"[:160],
                            "修该项检查或补依赖")
            )
    return report


def main(argv: Sequence[str] | None = None) -> int:
    import argparse

    # prog 必须写死：不写就按 sys.argv[0] 推，客户看到的是 `usage: __main__.py …`，
    # 那行命令抄下来跑不了（B229：usage 里只许出现真入口）。
    ap = argparse.ArgumentParser(prog="hos-ares doctor", description="部署自检")
    ap.add_argument("--root", default=str(Path(__file__).resolve().parents[1]))
    ap.add_argument("--only", nargs="*", default=[])
    ap.add_argument("--json", action="store_true")
    # B61：``python -m`` 调用时 argv 为 None，原实现传空列表 → 所有参数被静默忽略
    args = ap.parse_args(sys.argv[1:] if argv is None else list(argv))

    report = run_doctor(args.root, only=args.only)
    # Windows 控制台是 GBK：统一按 UTF-8 写出，避免 ✅/❌/替换字符触发 UnicodeEncodeError。
    # 用 getattr 兜一下：mypy 认为 sys.stdout 可能是任意 TextIO（无 reconfigure）。
    # B61：两个分支都要，``--json`` 此前因 argv 被忽略而从未真正执行过。
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if callable(reconfigure):
        reconfigure(encoding="utf-8", errors="replace")
    if args.json:
        print(json.dumps({"results": [r.as_dict() for r in report.results],
                          "green": report.green, "counts": report.by_status(),
                          "verdict": report.verdict(),
                          "exit": 0 if report.green else 1},
                         ensure_ascii=False, indent=2))
    else:
        print(report.render())
    return 0 if report.green else 1


if __name__ == "__main__":
    raise SystemExit(main())
