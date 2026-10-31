"""op_id 符号表（24 §2）。

**这里只有符号，没有路径。** 传输（方法/路径）只在
``config/bindings/service.yaml`` 里，路径值由部署环境的 ``.env`` 注入。
"""

from __future__ import annotations


class OP:
    """操作符号。代码里只许引用这些常量，不许写传输路径。"""

    # 2.1 任务
    TASK_SUBMIT = "task.submit"
    TASK_GET = "task.get"
    TASK_LIST = "task.list"
    TASK_CANCEL = "task.cancel"
    TASK_REQUEUE = "task.requeue"

    # 2.2 发现与证据
    FINDING_LIST = "finding.list"
    FINDING_GET = "finding.get"
    FINDING_FEEDBACK = "finding.feedback"
    CAPSULE_GET = "capsule.get"
    CAPSULE_DOWNLOAD = "capsule.download"
    CAPSULE_REPLAY = "capsule.replay"

    # 2.3 路由
    ROUTE_DISCOVER = "route.discover"
    ROUTE_LIST = "route.list"
    ROUTE_EXPLAIN = "route.explain"
    ROUTE_DIFF = "route.diff"

    # 2.4 审批
    APPROVAL_LIST = "approval.list"
    APPROVAL_DECIDE = "approval.decide"

    # 2.5 观测与运维
    STATS_TASK = "stats.task"
    STATS_ENGAGEMENT = "stats.engagement"
    COVERAGE = "coverage"
    BOUNDS = "bounds"
    HEALTHZ = "healthz"
    READYZ = "readyz"


ALL_OPS: tuple[str, ...] = tuple(
    v for k, v in vars(OP).items() if k.isupper() and isinstance(v, str)
)

#: B1 只实现对内最小面；其余 op 在后续批次接线，但**绑定必须先登记**（fail-closed）。
IMPLEMENTED_OPS: tuple[str, ...] = (OP.TASK_SUBMIT, OP.TASK_GET, OP.HEALTHZ, OP.READYZ)
