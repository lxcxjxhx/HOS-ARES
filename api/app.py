"""FastAPI 应用装配（B1 最小面：task.submit / task.get / healthz / readyz）。

按 ``op_id`` 注册，路径由绑定表 + 环境提供 —— 代码里没有一个路径字面量（27 N3）。
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from veripen_core.models import TaskState
from veripen_core.store import Store

from .bindings import BindingTable, load_table
from .ops import OP

CONFIG_BINDINGS_PATH = "config/bindings/service.yaml"

#: 错误码常量（24 §4 唯一清单；代码只许引用这里）
VP_BINDING_MISSING = "VP-1006"
VP_TASK_NOT_FOUND = "VP-5005"


class SubmitTaskBody(BaseModel):
    engagement_id: str
    profile: str
    profile_hash: str = ""
    asset_ids: list[str] = Field(default_factory=list)
    oracle_ids: list[str] = Field(default_factory=list)
    params: dict[str, Any] = Field(default_factory=dict)


def _error(status: int, code: str, message: str, **detail: Any) -> HTTPException:
    return HTTPException(
        status_code=status,
        detail={"error": {"code": code, "message": message, "retryable": False, "detail": detail}},
    )


def create_app(store: Store, table: BindingTable, env: dict[str, str] | None = None) -> FastAPI:
    """按绑定表解析路径并注册。任一 op 未解析即抛 ``BindingsUnresolved``（fail-closed）。"""
    app = FastAPI(title="VeriPen", version=table.api_version or "0")

    paths: dict[str, str] = {op: table.get(op).resolve_path(env) for op in (
        OP.TASK_SUBMIT,
        OP.TASK_GET,
        OP.HEALTHZ,
        OP.READYZ,
    )}

    def _submit(body: SubmitTaskBody) -> dict[str, Any]:
        if not body.asset_ids:
            raise _error(400, VP_BINDING_MISSING, "asset_ids 不能为空")
        created: list[dict[str, Any]] = []
        reused = False
        for asset_id in body.asset_ids:
            task_id, was_reused = store.submit_task(
                engagement_id=body.engagement_id,
                profile_name=body.profile,
                profile_hash=body.profile_hash,
                asset_id=asset_id,
                oracle_ids=body.oracle_ids,
                params=body.params,
            )
            reused = reused or was_reused
            created.append({"task_id": task_id, "reused": was_reused})
            break  # B1 一个请求一个资产；批量在 B2 的调度里做
        task_id = created[0]["task_id"]
        row = store.get_task(task_id) or {}
        return {
            "task_id": task_id,
            "state": row.get("state", TaskState.QUEUED.value),
            "idem_key": row.get("idem_key", ""),
            "reused": reused,
        }

    def _get(task_id: str) -> dict[str, Any]:
        # task_id 走查询参数：注册路径由绑定提供，代码里不拼任何路径段（27 N3）
        row = store.get_task(task_id)
        if row is None:
            raise _error(404, VP_TASK_NOT_FOUND, "task 不存在", task_id=task_id)
        return {
            "task_id": row["id"],
            "state": row["state"],
            "attempt": row["attempt"],
            "lease_until": row["lease_until"],
            "routeset_sha256": row["routeset_sha256"],
        }

    def _healthz() -> dict[str, str]:
        return {"status": "ok"}

    def _readyz() -> dict[str, Any]:
        try:
            store.count_by_state(TaskState.QUEUED)
        except Exception as exc:  # noqa: BLE001 - readyz 需要把依赖故障变成 503
            raise _error(503, VP_BINDING_MISSING, "存储层不可用", reason=type(exc).__name__)
        return {"status": "ready"}

    app.add_api_route(paths[OP.TASK_SUBMIT], _submit, methods=["POST"])
    app.add_api_route(paths[OP.TASK_GET], _get, methods=["GET"])
    app.add_api_route(paths[OP.HEALTHZ], _healthz, methods=["GET"])
    app.add_api_route(paths[OP.READYZ], _readyz, methods=["GET"])

    @app.middleware("http")
    async def _version_header(request: Request, call_next):  # type: ignore[no-untyped-def]
        response = await call_next(request)
        response.headers["X-Veripen-Version"] = table.api_version or "0"
        return response

    return app


def build_default_app() -> FastAPI:
    """生产装配：读绑定文件 + 环境 + 默认库路径。"""
    import os

    db_path = os.environ.get("VERIPEN_DB", "out/veripen.db")
    store = Store(db_path).init()
    return create_app(store, load_table(CONFIG_BINDINGS_PATH))
