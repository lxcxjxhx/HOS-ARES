# -*- coding: utf-8 -*-
"""
coop/client.py — CoopClient（手机端调用远程 Agent Server）

职责：
    手机端（控制端 / 报告终端）通过网络调用电脑端 Agent Server：
        connect(server_url, token) -> bool   建立连接（含鉴权校验）
        submit_task(task) -> dict            提交任务，返回 {task_id, status, ...}
        fetch_report(task_id) -> dict        查询 / 轮询任务状态与报告
        run_task(task) -> dict               便捷封装：submit + 轮询直到出报告

传输说明：
    - 本占位实现基于标准库 urllib.request（HTTP/HTTPS），零第三方依赖；
    - 若项目已安装 requests，可直接替换 _request() 内部实现（见下方真实实现注释）；
    - 若需低延迟推送，可在同一协议上扩展 WebSocket（见 run_task 注释）。

安全说明：
    - 每次请求携带 Authorization: Bearer <token>（见 security.TokenAuth）；
    - HTTPS / WireGuard VPN / 防中间人 见 security.py 与 README。
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any, Dict, Optional

from .protocol import (
    HEALTH_PATH,
    SUBMIT_PATH,
    REPORT_PATH_PREFIX,
    TaskStatus,
    TERMINAL_STATUSES,
    new_task_id,
)
from .security import TokenAuth


class CoopClient:
    """
    手机端 Agent Server 客户端。

    用法：
        client = CoopClient()
        ok = client.connect("https://192.168.1.10:9000", token="xxx")
        if ok:
            result = client.run_task("审计这个项目")
            print(result["report"])
    """

    def __init__(self, timeout: float = 30.0) -> None:
        self.base_url: Optional[str] = None
        self.auth: Optional[TokenAuth] = None
        self.timeout: float = timeout

    # ------------------------------------------------------------------
    # 连接
    # ------------------------------------------------------------------
    def connect(self, server_url: str, token: str) -> bool:
        """
        建立连接并完成鉴权校验。

        参数：
            server_url: 电脑 Agent Server 地址，如 "https://192.168.1.10:9000"。
            token:      共享鉴权令牌（与电脑端一致）。
        返回：
            True 表示连接成功且鉴权通过。
        """
        server_url = server_url.rstrip("/")
        self.base_url = server_url
        self.auth = TokenAuth(token)
        try:
            # 1) 健康检查（不鉴权）：确认服务可达
            data, _ = self._request("GET", HEALTH_PATH, authenticated=False)
            if not (data and data.get("status") == "ok"):
                return False
            # 2) 校验 token：请求受保护端点，401 表示 token 无效
            #    （用不存在的 task_id 探测：合法 token 得到 404，非法 token 得到 401）
            _, code = self._request("GET", f"{REPORT_PATH_PREFIX}__probe__")
            return code != 401
        except Exception as exc:  # 网络失败等
            print(f"[CoopClient] 连接失败: {exc}")
            return False

    # ------------------------------------------------------------------
    # 提交任务
    # ------------------------------------------------------------------
    def submit_task(self, task: str, task_type: Optional[str] = None,
                    meta: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        向远程 Agent Server 提交任务。

        参数：
            task:      自然语言任务文本。
            task_type: 可选任务类型提示（如 security_audit）。
            meta:      可选扩展元信息。
        返回：
            dict，形如 {"task_id": "...", "status": "pending", ...}。
        """
        if not self.base_url or not self.auth:
            raise RuntimeError("尚未连接，请先调用 connect()")
        payload = {
            "task": task,
            "task_type": task_type,
            "meta": meta or {},
        }
        data, _ = self._request("POST", SUBMIT_PATH, payload=payload)
        return data

    # ------------------------------------------------------------------
    # 查询 / 轮询报告
    # ------------------------------------------------------------------
    def fetch_report(self, task_id: str) -> Dict[str, Any]:
        """
        获取 / 轮询任务报告。

        参数：
            task_id: submit_task 返回的任务 ID。
        返回：
            dict，形如 {"task_id": "...", "status": "succeeded|running|failed",
                        "report": "...", "error": "..."}。
        """
        if not self.base_url or not self.auth:
            raise RuntimeError("尚未连接，请先调用 connect()")
        data, _ = self._request("GET", f"{REPORT_PATH_PREFIX}{task_id}")
        return data

    def run_task(self, task: str, poll_interval: float = 1.0,
                 max_polls: int = 60, **submit_kwargs) -> Dict[str, Any]:
        """
        【便捷】提交任务并轮询直到出现终端状态（succeeded/failed/unknown）。

        参数：
            task:          任务文本。
            poll_interval: 轮询间隔（秒）。
            max_polls:     最大轮询次数，超出则返回当前状态（超时兜底）。
        返回：
            最终（或超时时的）任务状态字典。

        真实实现建议（低延迟推送）：
            若需避免轮询延迟，可扩展 WebSocket 通道——服务端在任务完成时
            向手机端推送 report（协议数据结构不变），客户端用
            websocket-client / aiohttp 订阅。轮询实现与之可并存、可切换。
        """
        submitted = self.submit_task(task, **submit_kwargs)
        task_id = submitted.get("task_id")
        if not task_id:
            return submitted

        for _ in range(max_polls):
            resp = self.fetch_report(task_id)
            status = resp.get("status")
            if status in TERMINAL_STATUSES:
                return resp
            time.sleep(poll_interval)
        return resp  # 超时返回当前状态

    # ------------------------------------------------------------------
    # 底层 HTTP 请求（urllib 占位实现）
    # ------------------------------------------------------------------
    def _request(self, method: str, path: str,
                 payload: Optional[Dict[str, Any]] = None,
                 authenticated: bool = True):
        """
        发起 HTTP 请求并解析 JSON 响应。

        真实实现建议（替换为 requests）：
            ```
            import requests
            headers = {"Content-Type": "application/json"}
            if authenticated:
                headers["Authorization"] = self.auth.header_value()
            if method == "GET":
                r = requests.get(self.base_url + path, headers=headers, timeout=self.timeout)
            else:
                r = requests.post(self.base_url + path, json=payload,
                                  headers=headers, timeout=self.timeout)
            r.raise_for_status()
            return r.json()
            ```
        若走 WebSocket，可在此基础上额外建立 ws 通道并替换轮询逻辑。
        """
        url = self.base_url + path
        body = json.dumps(payload).encode("utf-8") if payload is not None else None

        req = urllib.request.Request(url, data=body, method=method)
        req.add_header("Content-Type", "application/json")
        if authenticated and self.auth:
            req.add_header("Authorization", self.auth.header_value())

        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8")
                return (json.loads(raw) if raw else {}), resp.status
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8")
            try:
                return json.loads(raw), exc.code
            except json.JSONDecodeError:
                return {"error": raw}, exc.code


if __name__ == "__main__":
    # 本机伪流程示例（不要求真实跨机通信）：
    #   直接构造 client，用占位 server 地址演示 submit_task / fetch_report 的调用形态。
    #   （真实端到端验证见 server.py 的 __main__：同进程启动 Server + Client 全链路。）
    demo = CoopClient()
    print("== CoopClient 占位演示（不发起真实网络请求）==")
    print("设计流程：")
    print("  1) connect(server_url, token)  →  建立连接并鉴权")
    print("  2) submit_task('审计这个项目')  →  返回 {'task_id', 'status', ...}")
    print("  3) fetch_report(task_id)       →  轮询直到 status=succeeded，读取 report")
    print("  4) 手机端将 report 作为报告终端展示给用户")
    print()
    print("端到端验证请运行：python -m coop.server")
