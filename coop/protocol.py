# -*- coding: utf-8 -*-
"""
coop/protocol.py — 手机-电脑协同通信协议

定义手机(控制端/报告终端) ↔ 电脑(Agent Server) 之间的消息结构：
    - submit_task 请求（提交任务）
    - task status 响应（任务状态）
    - report 响应（最终报告）
并给出 JSON Schema 占位定义、接口路径与 task_id 轮询机制说明。

本模块只做「协议定义」，不涉及具体网络实现（网络在 client.py / server.py）。
使用纯标准库（json / dataclasses），无第三方依赖。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# 协议版本 / 接口路径（REST 风格，均可通过 HTTPS 承载）
# ---------------------------------------------------------------------------
PROTOCOL_VERSION = "1.0"          # 协议版本，用于向后兼容判断

# 接口路径常量
HEALTH_PATH = "/api/health"             # GET  健康检查 / 连接校验
SUBMIT_PATH = "/api/tasks"              # POST 提交任务
REPORT_PATH_PREFIX = "/api/tasks/"      # GET  /api/tasks/{task_id} 查询状态/报告


# ---------------------------------------------------------------------------
# 任务状态（与 server 内部执行状态一致）
# ---------------------------------------------------------------------------
class TaskStatus:
    """任务状态枚举值。"""
    PENDING = "pending"      # 已接收，排队待处理
    RUNNING = "running"      # 处理/模型推理中
    SUCCEEDED = "succeeded"  # 成功，report 字段已就绪
    FAILED = "failed"        # 失败，error 字段携带原因
    UNKNOWN = "unknown"      # 未找到该任务


# ---------------------------------------------------------------------------
# 数据结构（dataclass 占位 + JSON 字典两种形态）
# ---------------------------------------------------------------------------
def new_task_id() -> str:
    """生成任务唯一 ID（手机端与电脑端共用）。"""
    return uuid.uuid4().hex[:12]


@dataclass
class SubmitTaskRequest:
    """
    submit_task 请求体。

    字段：
        task:        用户提交的自然语言任务文本
        task_type:   可选，任务类型提示（如 security_audit），留空由服务端识别
        meta:        可选扩展元信息（如手机端 App 版本、来源设备等）
    """
    task: str
    task_type: Optional[str] = None
    meta: Dict[str, Any] = field(default_factory=dict)


@dataclass
class TaskStatusResponse:
    """
    任务状态 / 报告响应体（GET /api/tasks/{task_id} 的返回）。

    字段：
        task_id:    任务唯一 ID
        status:     任务状态（TaskStatus）
        report:     最终报告（status=succeeded 时非空）
        error:      错误信息（status=failed 时非空）
        created_at: 创建时间（Unix 秒）
        updated_at: 最近更新时间（Unix 秒）
    """
    task_id: str
    status: str = TaskStatus.PENDING
    report: Optional[str] = None
    error: Optional[str] = None
    created_at: Optional[float] = None
    updated_at: Optional[float] = None


# ---------------------------------------------------------------------------
# JSON Schema 占位定义（后续可导入到 OpenAPI / 服务端校验）
# ---------------------------------------------------------------------------
SCHEMA_SUBMIT_REQUEST: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "task": {"type": "string", "minLength": 1, "description": "任务文本"},
        "task_type": {"type": ["string", "null"], "description": "任务类型提示"},
        "meta": {"type": "object", "description": "扩展元信息"},
    },
    "required": ["task"],
    "additionalProperties": True,
}

SCHEMA_TASK_STATUS_RESPONSE: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "task_id": {"type": "string"},
        "status": {
            "type": "string",
            "enum": [TaskStatus.PENDING, TaskStatus.RUNNING,
                     TaskStatus.SUCCEEDED, TaskStatus.FAILED, TaskStatus.UNKNOWN],
        },
        "report": {"type": ["string", "null"]},
        "error": {"type": ["string", "null"]},
        "created_at": {"type": ["number", "null"]},
        "updated_at": {"type": ["number", "null"]},
    },
    "required": ["task_id", "status"],
}

# 终端状态：轮询到这些状态即停止
TERMINAL_STATUSES = frozenset({TaskStatus.SUCCEEDED, TaskStatus.FAILED, TaskStatus.UNKNOWN})


# ---------------------------------------------------------------------------
# 序列化辅助
# ---------------------------------------------------------------------------
def request_to_dict(req: SubmitTaskRequest) -> Dict[str, Any]:
    """将 SubmitTaskRequest 序列化为 JSON 字典。"""
    return {
        "task": req.task,
        "task_type": req.task_type,
        "meta": req.meta,
    }


def parse_status_response(data: Dict[str, Any]) -> TaskStatusResponse:
    """将服务端返回的 JSON 字典解析为 TaskStatusResponse。"""
    return TaskStatusResponse(
        task_id=data.get("task_id", ""),
        status=data.get("status", TaskStatus.UNKNOWN),
        report=data.get("report"),
        error=data.get("error"),
        created_at=data.get("created_at"),
        updated_at=data.get("updated_at"),
    )


# ---------------------------------------------------------------------------
# task_id 轮询机制说明
# ---------------------------------------------------------------------------
POLLING_DOC = """
task_id 轮询机制说明
====================

1) 提交：手机端 POST /api/tasks 提交任务，服务端立即返回 202 + task_id，
   任务进入 pending/running 状态，异步在服务端后台执行（避免阻塞手机端）。

2) 轮询：手机端以固定间隔（如 1~3 秒）GET /api/tasks/{task_id}，
   读取 TaskStatusResponse.status：
     - pending / running → 尚未完成，继续轮询；
     - succeeded → 读取 report 字段作为最终报告并展示；
     - failed → 读取 error 字段并向用户提示；
     - unknown → 任务不存在，中止。

3) 终止条件：status 进入 succeeded / failed / unknown 任一「终端状态」即停止轮询，
   并设置最大轮询次数 / 超时兜底（见 client.run_task）。

为何用轮询而非长连接：
    - 手机端网络环境（WiFi/VPN）可能不稳定，轮询更健壮、易断线重连；
    - 服务端实现简单，天然兼容 HTTP/HTTPS；
    - 若后续需要更低延迟，可在同一协议上扩展 WebSocket 推送（见 client.py 注释）。
"""


if __name__ == "__main__":
    # 验证协议数据结构可正常构造 / 序列化 / 解析
    req = SubmitTaskRequest(task="审计这个项目", task_type="security_audit")
    print("请求体:", request_to_dict(req))

    resp = TaskStatusResponse(
        task_id="abc123", status=TaskStatus.SUCCEEDED,
        report="审计完成：发现 3 个中危漏洞", updated_at=1700000000.0,
    )
    parsed = parse_status_response(
        {"task_id": "abc123", "status": "succeeded",
         "report": "审计完成：发现 3 个中危漏洞", "updated_at": 1700000000.0}
    )
    print("解析状态:", parsed.status, "| 报告:", parsed.report)
    print(POLLING_DOC)
