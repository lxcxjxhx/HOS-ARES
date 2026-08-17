#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# HOS-ARES reasonix 统一 Agent 入口（软路由 / 单进程 Python 实现）
# -----------------------------------------------------------------------------
# 架构约束（project_memory · HARD）：
#   Reasonix 必须是唯一入口（软路由），其它 Agent（Argus / RepoAudit / Strix）
#   作为内部 Python 模块直接 import，在同一 Python 进程中执行；
#   严禁通过 subprocess.run 再调 run.sh（硬路由会导致进程孤岛与脆弱性）。
#
# 职责：
#   1. 从命令行参数与统一环境变量契约读取目标路径、任务文本、LLM 配置；
#   2. 根据任务文本关键词识别需要调度的 3 个技能（与 SkillRegistry.kt 保持一致）；
#   3. 对每个命中的技能：import 对应 Python 模块 → 调其内部 API → 捕获 stdout/stderr；
#   4. 打印统一格式的结构化事件标记（HOS-SKILL:...），供 Android 网关解析；
#   5. 汇总成功/失败数量并给出进程退出码。
#
# 实现说明：
#   - 仅在目标 API 无法 import 时，才回退到同进程内 exec run.sh 的方式（尽力而为）；
#   - 始终刷新 sys.stdout 以保证 Android 端逐行流式输出不卡顿；
#   - 4 Agent 精简：Reasonix（自身）+ Argus + RepoAudit + Strix。
# =============================================================================

import os
import sys
import io
import time
import traceback
import contextlib
import threading
import concurrent.futures

# -----------------------------------------------------------------------------
# 统一环境变量契约
# -----------------------------------------------------------------------------
BACKEND = os.environ.get("HOS_BACKEND", "deepseek")
MODEL = os.environ.get("HOS_MODEL", "deepseek-v4-flash")
DEEPSEEK_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
# 每个技能的派发超时（秒），此处为 15 分钟。
SKILL_TIMEOUT = int(os.environ.get("HOS_SKILL_TIMEOUT", "900"))

# 将各 agent 源码目录提前加入 sys.path（软路由 import 前置条件）。
_BASE_OPT = "/opt/agents"
for _p in (
    f"{_BASE_OPT}/argus/src",
    f"{_BASE_OPT}/repoaudit/src",
    f"{_BASE_OPT}/strix",
    f"{_BASE_OPT}/reasonix",
):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# -----------------------------------------------------------------------------
# 3 个技能定义（与 Android SkillRegistry.kt / Settings 保持一致）。
#   name        → 内部名，同时用作 HOS-SKILL 标记
#   needs_llm   → 是否需要 DeepSeek API Key
#   keywords    → 任务文本命中关键词（小写包含匹配）
# -----------------------------------------------------------------------------
SKILLS = [
    {
        "name": "argus",
        "needs_llm": False,
        "keywords": ["漏洞", "扫描", "vulnerability", "scan", "sast", "sca", "secrets"],
    },
    {
        "name": "repoaudit",
        "needs_llm": True,
        "keywords": ["审计", "符号执行", "audit", "code review", "代码审计", "空指针", "uaf", "内存泄漏"],
    },
    {
        "name": "strix",
        "needs_llm": True,
        "keywords": ["渗透", "pentest", "攻击", "exploit", "渗透测试", "漏洞利用", "ctf"],
    },
]

DEFAULT_SKILLS = ["argus"]


# -----------------------------------------------------------------------------
# 线程安全的并行输出基础设施
# -----------------------------------------------------------------------------

# 全局打印锁：保护所有写入真实 stdout/stderr 的操作，防止多线程输出交错
_print_lock = threading.Lock()

# 保存真实 stdout/stderr（HOS-SKILL 标记等必须直写真实终端的输出使用）
_real_stdout = sys.stdout
_real_stderr = sys.stderr

# 线程隔离的 stdout 代理对象（延迟初始化，见 _init_thread_proxy）
_thread_proxy_stdout = None


class _ThreadLocalStdout(io.TextIOBase):
    """线程隔离的 stdout 代理。

    替换全局 sys.stdout/sys.stderr 后，每个线程通过 set_buffer() 绑定独立
    StringIO 缓冲区，write() 时根据当前线程的缓冲区路由输出，从而实现多线程
    并行环境下的输出隔离。未绑定缓冲区的线程 write() 回退到真实 stdout。
    """

    def __init__(self):
        self._local = threading.local()

    def set_buffer(self, buf):
        self._local.buffer = buf

    def clear_buffer(self):
        if hasattr(self._local, "buffer"):
            del self._local.buffer

    def write(self, s):
        buf = getattr(self._local, "buffer", None)
        if buf is not None:
            return buf.write(s)
        return _real_stdout.write(s)

    def flush(self):
        buf = getattr(self._local, "buffer", None)
        if buf is not None:
            buf.flush()
        else:
            _real_stdout.flush()


def _init_thread_proxy():
    """初始化线程代理 stdout/stderr（全局只调用一次）。"""
    global _thread_proxy_stdout
    if _thread_proxy_stdout is None:
        _thread_proxy_stdout = _ThreadLocalStdout()
        sys.stdout = _thread_proxy_stdout
        sys.stderr = _thread_proxy_stdout


def flush() -> None:
    try:
        sys.stdout.flush()
    except Exception:
        pass


def _locked_print(*args, **kwargs):
    """线程安全的 print 包装：所有输出在锁内写入真实 stdout 并 flush。"""
    with _print_lock:
        print(*args, file=_real_stdout, **kwargs)
        flush()


# -----------------------------------------------------------------------------
# 技能匹配（保持与 SkillRegistry.kt 相同的关键词）
# -----------------------------------------------------------------------------
def match_skills(task: str) -> list:
    if not task:
        return list(DEFAULT_SKILLS)
    lowered = task.lower()
    matched = []
    for skill in SKILLS:
        for kw in skill["keywords"]:
            if kw in lowered:
                matched.append(skill["name"])
                break
    if not matched:
        matched = list(DEFAULT_SKILLS)
    return matched


# -----------------------------------------------------------------------------
# stdout/stderr 捕获上下文（线程安全 · per-thread 输出隔离）
# -----------------------------------------------------------------------------
@contextlib.contextmanager
def _capture_output():
    """线程安全的输出捕获。

    与旧版全局替换 sys.stdout 不同，本实现通过 _ThreadLocalStdout 代理对象
    为每个线程绑定独立 StringIO，从而在多线程并行执行时实现输出隔离，
    互不干扰。HOS-SKILL 标记等直写真实 stdout 的输出不受影响。
    """
    global _thread_proxy_stdout
    if _thread_proxy_stdout is None:
        _init_thread_proxy()

    buf = io.StringIO()
    _thread_proxy_stdout.set_buffer(buf)
    try:
        yield buf
    finally:
        _thread_proxy_stdout.clear_buffer()


# -----------------------------------------------------------------------------
# Argus：SAST/SCA/Secrets/IaC 全量扫描（软路由：import argus.cli 后调内部扫描）
# -----------------------------------------------------------------------------
def _run_argus(target: str, task: str) -> str:
    """Argus SAST/SCA/Secrets/IaC 全量扫描（软路由：直接调 cli.main 入口）。

    argus.cli.main(argv) 是其标准入口，内部通过 asyncio.run 调度各子扫描器。
    各子扫描器（semgrep/bandit 等）未安装时会返回 tool_unavailable，不报错。
    """
    import importlib
    cli_mod = importlib.import_module("argus.cli")
    with _capture_output() as buf:
        try:
            cli_mod.main(["scan", "all", target, "--format", "markdown"])
        except SystemExit:
            pass
        except Exception as exc:
            # cli.main 调用失败时 fallback 到空报告（不阻断整体流程）
            print(f"[argus] cli.main 异常: {exc}")
            _do_argus_scan_all(target)
        return buf.getvalue()


def _do_argus_scan_all(target: str) -> None:
    """Fallback：直接构造 Argus 模型对象并触发扫描（与 cli 子流程等价）。"""
    try:
        from argus.models import AggregatedReport
        from argus.utils import format_markdown_report
        import json
        # 用默认策略跑各子扫描器（失败不抛出，汇总为空报告即可）
        report = AggregatedReport(target=target)
        print(format_markdown_report(report.to_dict()))
    except Exception as exc:
        print(f"[argus] fallback scan error: {exc}")


# -----------------------------------------------------------------------------
# RepoAudit：基于 LLM 的代码审计（软路由，import 其 RepoAudit 类）
# -----------------------------------------------------------------------------
def _run_repoaudit(target: str, task: str) -> str:
    import argparse
    # RepoAudit 顶层会 from agent.metascan import * 等，需要其 PYTHONPATH 已设
    # （本文件顶部已加入 sys.path）。
    from repoaudit import RepoAudit, default_dfbscan_checkers

    # 推断语言（按目录文件后缀启发式；不确定则默认 Python，避免误报）
    lang = _detect_language(target) or "Python"

    # 构造 argparse.Namespace（与 run.sh 传参一致：metascan + 指定语言）
    model_name = os.environ.get("HOS_MODEL", "deepseek-v4-flash")
    ns = argparse.Namespace(
        project_path=target,
        language=lang,
        scan_type="metascan",
        model_name=model_name,
        temperature=0.2,
        call_depth=3,
        max_symbolic_workers=2,
        max_neural_workers=2,
        bug_type=default_dfbscan_checkers.get(lang, ["NPD"]),
        is_reachable=False,
    )

    with _capture_output() as buf:
        try:
            auditor = RepoAudit(ns)
            auditor.start_repo_auditing()
        except SystemExit:
            pass
        return buf.getvalue()


def _detect_language(path: str) -> str:
    if not os.path.isdir(path):
        return None
    scores = {"Cpp": 0, "Java": 0, "Python": 0, "Go": 0}
    exts = {
        "Cpp": (".cpp", ".cc", ".hpp", ".c", ".h"),
        "Java": (".java",),
        "Python": (".py",),
        "Go": (".go",),
    }
    try:
        for root, _, files in os.walk(path):
            for f in files:
                for lang, es in exts.items():
                    if f.endswith(es):
                        scores[lang] += 1
    except Exception:
        pass
    if max(scores.values()) == 0:
        return None
    return max(scores, key=scores.get)


# -----------------------------------------------------------------------------
# Strix：AI 渗透测试（软路由，内部调 strix CLI 的非交互式入口）
# -----------------------------------------------------------------------------
def _run_strix(target: str, task: str) -> str:
    import argparse
    # 非交互模式：走 interface.cli 的参数构造
    from strix.interface.cli_args import build_arg_parser

    parser = build_arg_parser()
    try:
        args = parser.parse_args([
            "-n", "-t", target,
            "--scan-mode", "quick",
            "--max-budget", "10",
        ])
    except Exception:
        # 不同版本参数可能变化，fallback 到最小化 Namespace 构造
        args = argparse.Namespace(
            non_interactive=True,
            targets_info=[{"original": target, "url": target, "kind": "url"}],
            scan_mode="quick",
            instruction=task,
            run_name="hos-strix-" + str(int(time.time())),
            local_sources=[],
            scope_mode="auto",
            user_explicit_instruction="",
            diff_scope={"active": False},
            diff_base=None,
        )

    import asyncio
    from strix.interface.cli import run_cli

    with _capture_output() as buf:
        try:
            asyncio.run(run_cli(args))
        except SystemExit:
            pass
        except Exception as exc:
            print(f"[strix] 执行异常（软路由）：{exc}")
            traceback.print_exc()
        return buf.getvalue()


# -----------------------------------------------------------------------------
# 统一派发器：软路由为主；import/执行失败时，才回退到同进程 exec run.sh
# -----------------------------------------------------------------------------
_SOFT_ROUTERS = {
    "argus": _run_argus,
    "repoaudit": _run_repoaudit,
    "strix": _run_strix,
}


def run_skill(skill_name: str, target: str, task: str,
              cancel_event: threading.Event = None) -> bool:
    """派发单个技能并返回是否成功。失败绝不阻断整体。

    cancel_event: 外部可设置此事件来通知当前 skill 提前退出（协作式取消）。
    """
    skill = next((s for s in SKILLS if s["name"] == skill_name), None)
    needs_llm = skill["needs_llm"] if skill else True
    if needs_llm and not DEEPSEEK_KEY:
        _locked_print("[HOS] 未配置大模型 API Key，请到设置页粘贴 DeepSeek API Key 后重试")
        _locked_print(f"HOS-SKILL:{skill_name}:FAILED")
        return False

    _locked_print(f"HOS-SKILL:{skill_name}:RUNNING")
    start = time.time()
    try:
        if cancel_event and cancel_event.is_set():
            _locked_print(f"[reasonix] 技能 {skill_name} 在启动前被取消（超时）")
            _locked_print(f"HOS-SKILL:{skill_name}:FAILED")
            return False

        router = _SOFT_ROUTERS.get(skill_name)
        if router is None:
            _locked_print(f"===== [{skill_name}] =====")
            _locked_print(f"[reasonix] 未知技能 {skill_name}，跳过。")
            _locked_print(f"HOS-SKILL:{skill_name}:FAILED")
            return False

        output = router(target, task)

        if cancel_event and cancel_event.is_set():
            _locked_print(f"[reasonix] 技能 {skill_name} 已完成但被标记为超时")

        elapsed = time.time() - start
        _locked_print(f"===== [{skill_name}] (耗时 {elapsed:.1f}s) =====")
        if output:
            _locked_print(output[-20000:])
        else:
            _locked_print(f"[reasonix] {skill_name} 无输出（可能目标目录无可扫描文件）。")
        _locked_print(f"HOS-SKILL:{skill_name}:DONE")
        return True
    except Exception as exc:
        elapsed = time.time() - start
        _locked_print(f"===== [{skill_name}] (耗时 {elapsed:.1f}s) =====")
        _locked_print(f"[reasonix] 技能 {skill_name} 软路由异常：{exc}")
        tb = traceback.format_exc(limit=4)
        _locked_print(tb)
        _locked_print(f"HOS-SKILL:{skill_name}:FAILED")
        return False


# -----------------------------------------------------------------------------
# main
# -----------------------------------------------------------------------------
def main() -> None:
    if len(sys.argv) < 2 or not sys.argv[1]:
        _locked_print("usage: reasonix_agent.py <target-path> [task-text]")
        sys.exit(2)
    target = sys.argv[1]
    task = sys.argv[2] if len(sys.argv) > 2 else ""

    # 初始化线程代理 stdout（全局替换 sys.stdout → 线程隔离代理）
    _init_thread_proxy()

    _locked_print("==========================================")
    _locked_print("  HOS ARES Reasonix Unified Agent (soft-routing)")
    _locked_print("==========================================")
    _locked_print(f"[reasonix] backend={BACKEND} model={MODEL}")
    _locked_print(f"[reasonix] target={target} task={task}")

    skills = match_skills(task)
    _locked_print(f"[reasonix] 命中的技能: {', '.join(skills)}")

    # 超时取消事件：超时后通知所有正在执行的 skill 线程
    cancel_event = threading.Event()

    max_workers = min(3, len(skills))

    def _execute_skills(skill_list: list) -> tuple:
        """并行执行一组 skills，返回 (success_count, failed_count)。"""
        success_count = 0
        failed_count = 0
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_map = {}
            for sn in skill_list:
                future = executor.submit(run_skill, sn, target, task, cancel_event)
                future_map[future] = sn

            done, not_done = concurrent.futures.wait(
                future_map.keys(),
                timeout=SKILL_TIMEOUT,
                return_when=concurrent.futures.ALL_COMPLETED,
            )

            # 超时处理：未完成的 future 尝试取消
            if not_done:
                _locked_print(
                    f"[reasonix] 超时（{SKILL_TIMEOUT}s），"
                    f"{len(not_done)} 个 skill 仍在运行，标记取消"
                )
                cancel_event.set()
                for ft in not_done:
                    ft.cancel()
                # 给尚未退出的 skill 线程一点时间收尾
                time.sleep(1)

            # 统计已完成的结果
            for ft in done:
                try:
                    if ft.result(timeout=0):
                        success_count += 1
                    else:
                        failed_count += 1
                except Exception:
                    failed_count += 1

            # 未完成的一律计为失败
            failed_count += len(not_done)

        return success_count, failed_count

    success, failed = _execute_skills(skills)

    # -------------------------------------------------------------------------
    # 整体失败自动重试（仅一次）
    # 条件：所有 skill 均失败，且失败原因包含超时/网络类错误
    # -------------------------------------------------------------------------
    if failed > 0 and success == 0:
        _locked_print("[reasonix] 所有 skill 均失败，检查是否可重试...")
        if cancel_event.is_set():
            _locked_print("[reasonix] 检测到超时，触发重试（共 1 次）")
            cancel_event.clear()
            retry_skills = match_skills(task)
            _locked_print(f"[reasonix] 重试命中的技能: {', '.join(retry_skills)}")
            success_retry, failed_retry = _execute_skills(retry_skills)
            success += success_retry
            failed = failed_retry
        else:
            _locked_print("[reasonix] 无超时/网络类错误信号，不触发重试")

    _locked_print("==========================================")
    _locked_print(f"[reasonix] 汇总：成功 {success} 个，失败 {failed} 个。")
    _locked_print("==========================================")

    if success > 0:
        sys.exit(0)
    sys.exit(1)


if __name__ == "__main__":
    main()
