"""B45 执行沙箱：容器隔离的唯一入口。

三条纪律（`H02 §2`）：

1. **唯一出口**：`act.*` 只能经 `SandboxRunner` 执行；没有运行时就是 `BLOCKED`，
   **不许静默回退到宿主 subprocess**（回退=把"没隔离"伪装成"隔离了"）；
2. **地址运行时注入**：镜像/命令里不出现目标地址，允许运行的 host 由 `RouteBinding` 在运行期解出；
3. **默认无网**：网络策略默认 `none`；要放开必须显式声明，`bridge` 不能"顺手"得到。
"""

from .policy import denied_hosts, hosts_of, scan_for_addresses
from .runner import DockerRunner, ExecResult, RuntimeUnavailable, select_executor

__all__ = [
    "DockerRunner",
    "ExecResult",
    "RuntimeUnavailable",
    "denied_hosts",
    "hosts_of",
    "scan_for_addresses",
    "select_executor",
]
