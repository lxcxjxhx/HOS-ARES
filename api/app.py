"""FastAPI 服务（`24 §2.1` 任务端点子集，B1 范围）。

B1 只交付：`POST /v1/tasks`（幂等）、`GET /v1/tasks/{id}`、`GET /v1/healthz`。
其余端点按批次卡逐批加入，不许提前扩。
"""

from __future__ import annotations

import time
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from veripen_core import (
    ERR_ASSET_NOT_IN_SCOPE,
    Budget,
    ScopeDenied,
    Store,
    Task,
    TaskCreateRequest,
    TaskCreateResponse,
    TaskState,
    VeripenError,
    idem_key,
    new_ulid,
    sha256_of,
    utc_now,
)

#: 允许出现在 task 创建里的 oracle id 前缀白名单——
#: 具体名单由 registry 提供，B1 阶段先做形状校验，避免未注册 oracle 混入。
_ORACLE_ID_RE = r"^[a-z][a-z0-9_]{2,63}$"


def create_app(store: Store, *, token: str | None = None, profile_loader: Any = None) -> FastAPI:
    """构造应用。`store` 注入以便测试用内存库。

    `token` 为 None 时跳过鉴权（仅测试与本地 demo）；生产必须给。
    """
    app = FastAPI(title="HOS-ARES VeriPen", version="0.1.0")
    app.state.store = store
    app.state.token = token
    app.state.profile_loader = profile_loader

    def _auth(x_veripen_token: str | None) -> None:
        if app.state.token is None:
            return
        if x_veripen_token != app.state.token:
            raise HTTPException(status_code=401, detail={"error": {"code": "VP-1005", "message": "bad token"}})

    @app.exception_handler(VeripenError)
    async def _veripen_error(_req: Request, exc: VeripenError) -> JSONResponse:
        return JSONResponse(status_code=exc.http_status(), content=exc.envelope())

    @app.get("/v1/healthz")
    async def healthz() -> dict[str, str]:
        """进程活着，不查依赖（`24 §2.4`）。"""
        return {"status": "ok"}

    @app.get("/v1/version")
    async def version() -> dict[str, str]:
        from veripen_core import __version__

        return {"version": __version__}

    @app.post("/v1/tasks", response_model=TaskCreateResponse)
    async def create_task(
        req: TaskCreateRequest,
        x_veripen_token: str | None = Header(default=None, alias="X-Veripen-Token"),
    ) -> TaskCreateResponse:
        """建任务（幂等）。重复提交返回 200 + 已有对象，`reused=true`（`24 §1`）。"""
        _auth(x_veripen_token)

        for oid in req.oracle_ids:
            import re

            if not re.match(_ORACLE_ID_RE, oid):
                raise VeripenError("VP-1001", f"invalid oracle id: {oid}", detail={"oracle_id": oid})

        # B1 阶段 scope 校验以 profile 加载器为准；未注入时按"单资产单任务"拆分。
        profile_doc: dict[str, Any] = {}
        if app.state.profile_loader is not None:
            profile_doc = app.state.profile_loader(req.profile)
            allowed = {a["id"] for a in profile_doc.get("scope", {}).get("assets", [])}
            for aid in req.asset_ids:
                if allowed and aid not in allowed:
                    raise ScopeDenied(
                        ERR_ASSET_NOT_IN_SCOPE,
                        "asset not in scope",
                        detail={"asset_id": aid},
                        hint="把该资产加入 profile 的 scope.assets，或从请求里移除",
                    )

        phash = sha256_of(profile_doc) if profile_doc else "sha256:" + "0" * 64

        # 一个请求可能带多个资产 × 多个 oracle；每个组合一个 task，
        # 这样 idem_key 才是 `01 §6` 定义的粒度。
        # B1 的响应体只回第一条（接口形状先定），完整列表在 GET /v1/tasks。
        first: TaskCreateResponse | None = None
        for aid in req.asset_ids:
            for oid in req.oracle_ids:
                key = idem_key(engagement_id=req.engagement_id, asset_id=aid, oracle_id=oid, params=req.params)
                existing = store.find_task_by_idem(key)
                if existing is not None:
                    resp = TaskCreateResponse(
                        task_id=existing.id,
                        state=existing.state,
                        idem_key=existing.idem_key,
                        reused=True,
                        created_at=_rfc3339(existing.created_at),
                    )
                else:
                    ts = utc_now()
                    task = Task(
                        id=new_ulid(),
                        engagement_id=req.engagement_id,
                        idem_key=key,
                        profile_name=req.profile,
                        profile_hash=phash,
                        asset_id=aid,
                        oracle_ids=[oid],
                        state=TaskState.QUEUED,
                        budget=Budget(),
                        result={"params": req.params} if req.params else None,
                        created_at=ts,
                        updated_at=ts,
                    )
                    # 并发下可能被别的请求抢先插入：插入失败则回退为"复用"。
                    if not store.insert_task(task):
                        again = store.find_task_by_idem(key)
                        assert again is not None, "idem_key collision but no row found"
                        task = again
                    resp = TaskCreateResponse(
                        task_id=task.id,
                        state=task.state,
                        idem_key=task.idem_key,
                        reused=False,
                        created_at=_rfc3339(task.created_at),
                    )
                if first is None:
                    first = resp

        assert first is not None, "no task created"
        return first

    @app.get("/v1/tasks/{task_id}")
    async def get_task(
        task_id: str,
        x_veripen_token: str | None = Header(default=None, alias="X-Veripen-Token"),
    ) -> dict[str, Any]:
        _auth(x_veripen_token)
        task = store.get_task(task_id)
        if task is None:
            raise VeripenError("VP-3005", f"task not found: {task_id}", detail={"task_id": task_id})
        return {
            "task_id": task.id,
            "engagement_id": task.engagement_id,
            "profile": task.profile_name,
            "profile_hash": task.profile_hash,
            "asset_id": task.asset_id,
            "oracle_ids": task.oracle_ids,
            "state": task.state.value,
            "attempt": task.attempt,
            "lease_until": task.lease_until,
            "budget": task.budget.model_dump(),
            "result": task.result,
            "idem_key": task.idem_key,
            "created_at": _rfc3339(task.created_at),
            "updated_at": _rfc3339(task.updated_at),
        }

    @app.get("/v1/tasks")
    async def list_tasks(
        engagement_id: str | None = None,
        state: str | None = None,
        limit: int = 50,
        x_veripen_token: str | None = Header(default=None, alias="X-Veripen-Token"),
    ) -> dict[str, Any]:
        _auth(x_veripen_token)
        if limit < 1 or limit > 200:
            raise VeripenError("VP-1001", "limit must be 1..200")
        rows = store._conn.execute(  # noqa: SLF001 — 同包内部访问，避免为 B1 扩 store 接口
            "SELECT * FROM tasks WHERE (? IS NULL OR engagement_id=?) AND (? IS NULL OR state=?) "
            "ORDER BY created_at DESC, id LIMIT ?",
            (engagement_id, engagement_id, state, state, limit),
        ).fetchall()
        return {
            "items": [
                {
                    "task_id": r["id"],
                    "state": r["state"],
                    "asset_id": r["asset_id"],
                    "oracle_ids": r["oracle_ids"],
                    "created_at": _rfc3339(r["created_at"]),
                }
                for r in rows
            ],
            "next_cursor": None,
        }

    return app


def _rfc3339(epoch_s: int) -> str:
    """全部 UTC RFC3339（`24 §1`）。"""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch_s))
