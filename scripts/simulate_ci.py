"""模拟 CI 环境跑单测：隐藏容器执行体与 WSL，看哪些用例会因此红。

**为什么需要这个脚本**：本机有 WSL + podman，`select_executor()` 返回容器档执行体；
而 GitHub 的 windows runner 没有——parity/doctor 的数字会变。
我在 B562 就栽在这里（写了要求"精确相等"的判据）。

**已知边界**：这个脚本用模块级 monkeypatch 改 `_docker_bin` / `_wsl_exe`，
而 `test_sandbox_wsl.py` 里那几条用例**自己也要替换这两个函数**——
两边互相覆盖，会报出假的失败（例如 `test_wsl_container_wins_over_job_object`）。
那几条在真 CI 上是好的。所以：**只把这里的失败当线索，不当结论**。

用法：``python scripts/simulate_ci.py [pytest 参数...]``
默认跑 `tests/unit`。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: 在子进程里先执行这段，把环境改成"没有容器执行体"。
#:
#: **保留 `JobObjectExecutor.available`**（B565 的教训）：
#: GitHub 的 windows runner 上 Job Object 是**可用**的，只有 docker/podman 没有。
#: 上一版把它也禁掉了，于是模拟出来的环境比真 CI 更严 ——
#: 结果真 CI 上红的那条（`doc_ok >= ok`）在模拟里没暴露出来。
#: 模拟环境**过严和过松一样有害**：过严会让你以为已经验过了。
_BOOTSTRAP = r"""
import sys
sys.path.insert(0, ".")
import hos_ares_core.sandbox.runner as R
R._docker_bin = lambda: None      # 宿主没有 docker/podman
R._wsl_exe = lambda: None         # 也没有 WSL
# JobObjectExecutor 保持原样：CI 上它有

import pytest
sys.exit(pytest.main(sys.argv[1:]))
"""


def main(argv: list[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:]) or ["tests/unit"]
    proc = subprocess.run(
        [sys.executable, "-c", _BOOTSTRAP, *args],
        cwd=str(ROOT),
    )
    return int(proc.returncode)


if __name__ == "__main__":
    raise SystemExit(main())
