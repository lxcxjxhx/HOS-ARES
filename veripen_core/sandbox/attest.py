"""沙箱执行体的**可离线验证**部分（B127）。

E1「真隔离」需要容器运行时，本机没有，永远未验。但执行体还有几件事**不需要容器**也能验：

| 能验 | 怎么验 |
|---|---|
| 超时真的会杀掉 | 让它跑一个明显超时的命令，看是不是 `blocked` |
| 退出码不被吞 | 让它 `exit 3`，看回的是不是 3 |
| 输出被捕获 | 让它打一行，看 stdout 里有没有 |
| **隔离等级没被吹大** | 结果里的 `isolation` 必须与执行体自报一致 |

最后一条最重要：`job-object` 不是 `container`。把资源隔离说成真隔离，
比"没验过"更糟——所以这里给出的是**否证**：它证明"当前不是容器隔离"。
"""

from __future__ import annotations

from typing import Any

from .runner import ExecResult, Executor

#: 超时探针：跑得比 timeout 久，必须被拦。
SLEEP_CMD = ("python", "-c", "import time; time.sleep(30)")
#: 退出码探针：非零退出码必须原样回来。
EXIT_CMD = ("python", "-c", "raise SystemExit(3)")
#: 输出探针：stdout 必须被捕获。
ECHO_CMD = ("python", "-c", "print('veripen-attest')")


def _run(executor: Executor, argv: tuple[str, ...], *, timeout_s: int) -> ExecResult:
    return executor.run(list(argv), image="", timeout_s=timeout_s)


def attest(executor: Executor) -> dict[str, Any]:
    """跑三条探针，返回**事实**（不是评分）。失败就是 False，不猜。"""
    facts: dict[str, Any] = {
        "executor": getattr(executor, "name", "unknown"),
        "available": bool(executor.available()),
    }
    if not facts["available"]:
        return {**facts, "checked": False, "reason": "executor_unavailable"}

    echo = _run(executor, ECHO_CMD, timeout_s=20)
    facts["stdout_captured"] = "veripen-attest" in (echo.stdout or "")

    exit_probe = _run(executor, EXIT_CMD, timeout_s=20)
    facts["exit_code_preserved"] = exit_probe.exit_code == 3

    slow = _run(executor, SLEEP_CMD, timeout_s=2)
    facts["timeout_enforced"] = slow.outcome == "blocked" or (
        slow.exit_code not in (None, 0) and slow.outcome != "ok"
    )

    # 隔离等级：只信执行体自报，并明确"这不是容器隔离"
    facts["isolation"] = str(slow.isolation or exit_probe.isolation or "")
    facts["isolation_is_container"] = facts["isolation"] == "container"
    facts["checked"] = True
    return facts


def verdict(facts: dict[str, Any]) -> tuple[bool, str]:
    """把事实翻成"能不能交付"的一句话。**没验容器就说没验**。"""
    if not facts.get("checked"):
        return False, "执行体不可用：任何执行都应报 BLOCKED"
    broken = [
        name for name in ("stdout_captured", "exit_code_preserved", "timeout_enforced")
        if not facts.get(name)
    ]
    if broken:
        return False, f"执行体行为异常：{', '.join(broken)}"
    if facts.get("isolation_is_container"):
        return True, "容器隔离（E1 可用此执行体验证）"
    return False, (
        f"执行体只会 {facts.get('isolation') or '不明'} 隔离："
        "超时/退出码/输出可验，**真隔离仍未验**"
    )


__all__ = ["ECHO_CMD", "EXIT_CMD", "SLEEP_CMD", "attest", "verdict"]
