# -*- coding: utf-8 -*-
"""
coop/server.py — CoopServer（电脑端 Agent Server）

职责：
    电脑端接收手机端提交的任务，调用本地 gateway / 调度器（或 GPU 模型）
    执行重型任务，并返回报告。手机端作为报告终端展示结果。

流程：
    启动服务 → 接收手机端 submit_task 请求 → 生成 task_id 并异步处理
    → 处理中调用 gateway/scheduler 或 GPU 模型 → 生成报告
    → 手机端通过 task_id 轮询 fetch_report 拿到最终报告。

传输说明：
    - 本占位实现基于标准库 http.server（ThreadingHTTPServer），零第三方依赖；
    - 支持 HTTPS（传入 certfile/keyfile 即启用 TLS），详见 load_ssl_context。

真实实现注释（如何接入本地 LLM/GPU 模型）：
    见下方 _run_model() 与 MODE_INTEGRATION_DOC，覆盖 vLLM / ollama 等方案。
"""

from __future__ import annotations

import json
import ssl
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Optional
from urllib.parse import urlparse

from .protocol import (
    HEALTH_PATH,
    SUBMIT_PATH,
    REPORT_PATH_PREFIX,
    TaskStatus,
    new_task_id,
)
from .security import TokenAuth, load_ssl_context


# 处理真实 LLM/GPU 模型接入的说明文档
MODE_INTEGRATION_DOC = """
如何接入本地 LLM / GPU 模型（真实实现）
======================================

本脚手架的 _run_model() 为占位：默认尝试调用 gateway.AgentGateway，
若不可用则返回一条「模拟 GPU 模型处理」的占位报告。

要替换为真实 GPU 推理，任选一种方案并在 _run_model() 中接入：

A. ollama（最简单，本地开箱即用）：
    1. 电脑端安装 ollama：ollama pull qwen3:14b（或 deepseek-r1 等）；
    2. 调用其本地 HTTP API：
       POST http://localhost:11434/api/generate
       {"model": "qwen3:14b", "prompt": task, "stream": false}
    3. 在 _run_model() 中解析 resp["response"] 作为报告。

B. vLLM（高吞吐，适合多并发 / 长上下文）：
    1. 用 vLLM 起 OpenAI 兼容服务：
       vllm serve Qwen/Qwen3-14B-Instruct --port 8000
    2. 客户端用 OpenAI 协议调用 POST /v1/chat/completions；
    3. 在 _run_model() 中用 requests / openai SDK 请求并取 choices[0].message.content。

C. 云端模型（Claude / DeepSeek 官方 API）：
    - 电脑端作为代理，把手机端任务转发给云端 LLM API，
      既保留手机端「轻量化」，又能获得最强模型能力。

D. 复用 HOS-ARES 现有调度：
    - 本项目已有 gateway.AgentGateway（统一 Agent 入口）。
    _run_model() 已尝试调用它：gateway 内部会做任务识别与调度，
    若其下层接入 Reasonix / 各 Agent，则重型任务在此完成。
"""


class CoopServer:
    """
    电脑端 Agent Server。

    用法：
        server = CoopServer(token="xxx")
        server.start(host="0.0.0.0", port=9000)   # 阻塞运行
    """

    def __init__(self, token: Optional[str] = None,
                 certfile: Optional[str] = None, keyfile: Optional[str] = None) -> None:
        """
        参数：
            token:     共享鉴权令牌；None 时自动生成。
            certfile:  可选 HTTPS 证书路径；提供时启用 TLS。
            keyfile:   可选 HTTPS 私钥路径。
        """
        self.auth = TokenAuth(token)
        self.certfile = certfile
        self.keyfile = keyfile
        self.tasks: Dict[str, Dict[str, Any]] = {}      # task_id -> 任务状态字典
        self._lock = threading.Lock()
        self.httpd: Optional[ThreadingHTTPServer] = None
        self.port: Optional[int] = None

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------
    def start(self, host: str = "0.0.0.0", port: int = 9000) -> None:
        """
        启动服务，接收手机端任务（阻塞运行）。

        参数：
            host: 监听地址；建议监听 VPN 内网 IP 而非公网。
            port: 监听端口。
        """
        handler = self._make_handler()
        self.httpd = ThreadingHTTPServer((host, port), handler)
        self.httpd.coop = self                       # 让 handler 能访问本 Server
        self.port = self.httpd.server_address[1]     # 记录实际端口（port=0 时为随机）

        # 可选：启用 HTTPS
        if self.certfile and self.keyfile:
            ctx = load_ssl_context(self.certfile, self.keyfile)
            self.httpd.socket = ctx.wrap_socket(self.httpd.socket, server_side=True)
            scheme = "https"
        else:
            scheme = "http"

        print(f"[CoopServer] {scheme}://{host}:{self.port} 已启动 "
              f"(token={self.auth.token})")
        try:
            self.httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n[CoopServer] 已停止")
            self.httpd.server_close()

    def shutdown(self) -> None:
        """停止服务（供同进程内联测试使用）。"""
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()

    # ------------------------------------------------------------------
    # 请求处理（由 handler 转发）
    # ------------------------------------------------------------------
    def handle_request(self, handler: BaseHTTPRequestHandler) -> None:
        """根据方法 / 路径分发请求。"""
        parsed = urlparse(handler.path)
        method = handler.command
        path = parsed.path

        if method == "GET" and path == HEALTH_PATH:
            self._send_json(handler, 200, {"status": "ok", "version": "1.0"})
            return

        if method == "GET" and path.startswith(REPORT_PATH_PREFIX):
            task_id = path[len(REPORT_PATH_PREFIX):]
            if not task_id:
                self._send_json(handler, 400, {"error": "缺少 task_id"})
                return
            self._handle_report(handler, task_id)
            return

        if method == "POST" and path == SUBMIT_PATH:
            if not self._require_auth(handler):
                return
            self._handle_submit(handler)
            return

        self._send_json(handler, 404, {"error": "未找到接口", "path": path})

    # ------------------------------------------------------------------
    # 各接口实现
    # ------------------------------------------------------------------
    def _handle_submit(self, handler: BaseHTTPRequestHandler) -> None:
        """接收 submit_task 请求：创建任务并异步处理，立即返回 task_id。"""
        try:
            body = self._read_json(handler)
            task = (body or {}).get("task", "")
            if not task or not task.strip():
                self._send_json(handler, 400, {"error": "task 不能为空"})
                return
        except Exception as exc:
            self._send_json(handler, 400, {"error": f"请求体解析失败: {exc}"})
            return

        task_id = new_task_id()
        now = time.time()
        with self._lock:
            self.tasks[task_id] = {
                "task_id": task_id,
                "task": task,
                "task_type": (body or {}).get("task_type"),
                "meta": (body or {}).get("meta") or {},
                "status": TaskStatus.PENDING,
                "report": None,
                "error": None,
                "created_at": now,
                "updated_at": now,
            }

        # 异步处理（占位线程），手机端可立即轮询状态
        threading.Thread(target=self._process, args=(task_id,), daemon=True).start()

        self._send_json(handler, 202, {
            "task_id": task_id,
            "status": TaskStatus.PENDING,
            "message": "任务已接收，请轮询 /api/tasks/{task_id}",
        })

    def _handle_report(self, handler: BaseHTTPRequestHandler, task_id: str) -> None:
        """返回任务状态 / 报告。"""
        if not self._require_auth(handler):
            return
        with self._lock:
            task = self.tasks.get(task_id)
        if not task:
            self._send_json(handler, 404, {
                "task_id": task_id, "status": TaskStatus.UNKNOWN,
                "error": "任务不存在",
            })
            return
        self._send_json(handler, 200, {
            "task_id": task["task_id"],
            "status": task["status"],
            "report": task["report"],
            "error": task["error"],
            "created_at": task["created_at"],
            "updated_at": task["updated_at"],
        })

    # ------------------------------------------------------------------
    # 任务执行（占位 GPU 模型 / 接入 gateway）
    # ------------------------------------------------------------------
    def _process(self, task_id: str) -> None:
        """后台处理任务：置为 running → 调用模型/gateway → 置为 succeeded/failed。"""
        with self._lock:
            task = self.tasks[task_id]
            task["status"] = TaskStatus.RUNNING
            task["updated_at"] = time.time()
        try:
            time.sleep(1.0)                      # 模拟模型推理耗时，便于演示轮询
            report = self._run_model(task["task"], task.get("task_type"))
            with self._lock:
                task["report"] = report
                task["status"] = TaskStatus.SUCCEEDED
                task["updated_at"] = time.time()
        except Exception as exc:
            with self._lock:
                task["error"] = str(exc)
                task["status"] = TaskStatus.FAILED
                task["updated_at"] = time.time()

    def _run_model(self, task: str, task_type: Optional[str]) -> str:
        """
        占位实现：调用本地 gateway/调度器；不可用时返回模拟 GPU 模型处理结果。

        真实实现注释：见模块级 MODE_INTEGRATION_DOC，
        可选方案：ollama / vLLM / 云端 Claude·DeepSeek / 复用 gateway.AgentGateway。
        """
        # 方案 D：优先复用现有 gateway（其内部做任务识别与调度）
        try:
            from gateway.agent_gateway import AgentGateway
            gateway = AgentGateway()
            result = gateway.submit(task)
            if result.report:
                return result.report
        except Exception as exc:  # gateway 不可用则优雅降级为占位
            gateway_error = str(exc)
        else:
            gateway_error = "gateway 未产出报告"

        # 方案 A/B/C：接入真实 GPU 模型（此处为占位文本）
        return (
            f"[占位·模拟GPU模型处理] 任务类型：{task_type or 'general'}；任务：{task}\n"
            f"—— 真实实现将接入 vLLM/ollama 上的 Qwen3/DeepSeek/Claude 重型推理，"
            f"或复用 gateway.AgentGateway。当前已优雅降级（{gateway_error}）。"
        )

    # ------------------------------------------------------------------
    # 鉴权 / 辅助
    # ------------------------------------------------------------------
    def _require_auth(self, handler: BaseHTTPRequestHandler) -> bool:
        """校验 Authorization 头；失败则返回 401 并返回 False。"""
        if self.auth.check_header(handler.headers.get("Authorization")):
            return True
        self._send_json(handler, 401, {"error": "未授权：token 无效"})
        return False

    def _make_handler(self):
        """构造绑定到本 Server 的 HTTP 处理器类。"""
        class _Handler(BaseHTTPRequestHandler):
            server_version = "HOS-ARES-CoopServer/1.0"

            def do_GET(self):
                self.server.coop.handle_request(self)

            def do_POST(self):
                self.server.coop.handle_request(self)

            def log_message(self, fmt, *args):
                print(f"[CoopServer] {self.address_string()} {fmt % args}")

        return _Handler

    @staticmethod
    def _read_json(handler: BaseHTTPRequestHandler) -> Dict[str, Any]:
        length = int(handler.headers.get("Content-Length") or 0)
        raw = handler.rfile.read(length) if length else b""
        return json.loads(raw.decode("utf-8")) if raw else {}

    @staticmethod
    def _send_json(handler: BaseHTTPRequestHandler, code: int, data: Dict[str, Any]) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        handler.send_response(code)
        handler.send_header("Content-Type", "application/json; charset=utf-8")
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)


# ---------------------------------------------------------------------------
# 验证示例：同进程内启动 Server + 用 CoopClient 走通「提交→轮询→出报告」
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    from .client import CoopClient

    print("== HOS-ARES 手机-电脑协同 端到端验证（本机模拟，不跨机）==")
    print("架构：手机(控制端) → WiFi/VPN → 电脑 Agent Server → GPU 模型 → 报告回传\n")

    TOKEN = "demo-token-1234"
    server = CoopServer(token=TOKEN)

    # 在独立线程中启动服务（port=0 取随机空闲端口）
    srv_thread = threading.Thread(
        target=server.start, kwargs={"host": "127.0.0.1", "port": 0}, daemon=True
    )
    srv_thread.start()

    # 等待服务就绪（拿到实际端口）
    while server.port is None:
        time.sleep(0.05)
    base_url = f"http://127.0.0.1:{server.port}"
    print(f"Server 监听: {base_url}")

    # 手机端客户端：连接 → 提交 → 轮询 → 展示报告
    client = CoopClient()
    ok = client.connect(base_url, TOKEN)
    print(f"[1] connect({base_url}, token)  →  {ok}")
    assert ok, "连接失败"

    submitted = client.submit_task("审计这个项目", task_type="security_audit")
    task_id = submitted.get("task_id")
    print(f"[2] submit_task('审计这个项目')  →  task_id={task_id}, status={submitted.get('status')}")

    print("[3] fetch_report 轮询中...")
    result = client.run_task("审计这个项目", task_type="security_audit", poll_interval=0.3)
    print(f"[4] 最终状态: {result.get('status')}")
    print("    手机端（报告终端）展示报告:")
    print("-" * 60)
    print(result.get("report"))
    print("-" * 60)

    # 错误 token 应被拒绝（connect 会校验 token）
    bad = CoopClient()
    print(f"[5] 错误 token 连接 → {bad.connect(base_url, 'wrong-token')}")

    server.shutdown()
    print("\n== 验证完成，Server 已关闭 ==")
