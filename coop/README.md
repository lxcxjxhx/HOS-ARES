# HOS-ARES 手机-电脑协同模式（coop/）

> 手机（控制端 / Agent 入口 / 报告终端）通过 WiFi/VPN 连接电脑 Agent Server，
> 电脑端用 GPU 模型（Qwen3 / DeepSeek / Claude）执行重型任务，手机端展示报告。

## 为什么需要协同模式

HOS-ARES 是 Android 应用，手机性能有限：
- 无法在手机本地流畅运行大参数 GPU 模型；
- 重型任务（安全审计、代码分析、长文本推理）需要更强的算力。

**协同模式**把「控制 / 交互 / 报告展示」放在手机，把「重计算 / 模型推理」放在电脑：

```
┌────────────┐   WiFi / VPN    ┌──────────────────┐   GPU 模型   ┌──────────────┐
│  手机       │ ───────────────▶ │  电脑 Agent Server │ ───────────▶ │ Qwen3/DeepSeek│
│  控制端 /   │                 │  (coop/server.py) │              │ Claude (vLLM/ │
│  报告终端    │ ◀─────────────── │                   │ ◀─────────── │ ollama/API)   │
│ (coop/client.py)│  报告回传    │  task_id 轮询     │              └──────────────┘
└────────────┘                 └──────────────────┘
     ▲
     │ 用户交互
   HOS-ARES App
```

**角色划分：**
- **手机（控制端）**：用户输入任务、作为 Agent 入口、展示最终报告（报告终端）。
- **电脑（Agent Server）**：接收任务、调用 gateway/调度器或 GPU 模型执行重型任务、返回报告。
- **GPU 模型**：Qwen3 / DeepSeek / Claude 等，运行在电脑本地（vLLM / ollama）或云端 API。

## 目录结构与职责

| 文件 | 职责 |
| --- | --- |
| `coop/client.py` | 手机端 `CoopClient`：连接 / 提交任务 / 轮询报告（基于标准库 urllib，零依赖） |
| `coop/server.py` | 电脑端 `CoopServer`：接收任务、异步处理、返回报告（基于 http.server，零依赖，支持 HTTPS） |
| `coop/protocol.py` | 通信协议：请求 / 响应数据结构、JSON Schema 占位、task_id 轮询机制说明 |
| `coop/security.py` | 安全措施：Token 鉴权、HTTPS / WireGuard VPN、防中间人（占位实现） |
| `coop/README.md` | 本文件：协同模式架构与协议说明 |

## 通信协议

采用 **REST 风格 + JSON + task_id 轮询**（详见 `protocol.py`）：

| 接口 | 方法 | 说明 |
| --- | --- | --- |
| `/api/health` | GET | 健康检查 / 连接校验（`connect` 使用） |
| `/api/tasks` | POST | 提交任务，返回 `202 + task_id`，异步处理 |
| `/api/tasks/{task_id}` | GET | 查询任务状态 / 报告（轮询用） |

**核心数据结构（JSON）：**

submit_task 请求：
```json
{
  "task": "审计这个项目",
  "task_type": "security_audit",
  "meta": { "source": "HOS-ARES-Android" }
}
```

task status / report 响应：
```json
{
  "task_id": "a1b2c3d4e5f6",
  "status": "succeeded",        // pending | running | succeeded | failed | unknown
  "report": "审计完成：发现 3 个中危漏洞...",
  "error": null,
  "created_at": 1700000000.0,
  "updated_at": 1700000000.0
}
```

**task_id 轮询机制**（详见 `protocol.POLLING_DOC`）：
1. 手机 POST 提交 → 得到 `task_id`，服务端后台异步执行；
2. 手机以固定间隔 GET `/api/tasks/{task_id}` 轮询 `status`；
3. 到 `succeeded` 读取 `report` 展示；到 `failed` 读取 `error` 提示；
4. 到 `succeeded / failed / unknown` 任一终端状态即停止，并设超时兜底。

## 安全措施（详见 `security.py`）

| 措施 | 说明 |
| --- | --- |
| Token 鉴权 | 每请求携带 `Authorization: Bearer <token>`，服务端恒定时间比较校验 |
| 传输加密 | 首选 **WireGuard VPN**（内网隧道，推荐）；公网场景用 **HTTPS**（`server.py` 传入 cert/key 即启用 TLS） |
| 防中间人 | HTTPS + 受信任 CA / 证书指纹固定（`verify_server_cert` 占位） |

## 快速验证（本机模拟，不跨机）

所有模块使用 Python 标准库，**零第三方依赖**，直接运行：

```powershell
# 1) 协议数据结构验证
python -m coop.protocol

# 2) 安全鉴权验证
python -m coop.security

# 3) 端到端验证：同进程启动 Server + 用 CoopClient 提交任务并轮询出报告
python -m coop.server
```

`python -m coop.server` 会打印完整流程：
```
[1] connect(http://127.0.0.1:PORT, token)  →  True
[2] submit_task('审计这个项目')  →  task_id=..., status=pending
[3] fetch_report 轮询中...
[4] 最终状态: succeeded    手机端（报告终端）展示报告...
[5] 错误 token 连接 → False
```

## 真实接入指南

### 1. 手机端（`client.py`）
- 默认基于 `urllib`；若项目已装 `requests`，按 `_request()` 中的注释替换即可；
- 如需低延迟，可在同一协议上扩展 WebSocket 推送（见 `client.run_task` 注释）。

### 2. 电脑端（`server.py`）
- 默认占位：尝试调用 `gateway.AgentGateway`，不可用则返回模拟 GPU 处理结果；
- 真实模型接入见 `server.MODE_INTEGRATION_DOC`，可选：
  - **ollama**：`ollama pull qwen3:14b`，调用 `POST /api/generate`；
  - **vLLM**：`vllm serve Qwen/Qwen3-14B-Instruct`，用 OpenAI 协议调用；
  - **云端 Claude / DeepSeek**：电脑作为代理转发云端 API。

### 3. 部署（WireGuard 网络拓扑）
- 电脑端 `server.start("0.0.0.0", 9000)`，建议监听 VPN 内网 IP；
- 手机端 `client.connect("http://<电脑VPN-IP>:9000", token)`；
- 参考 `.env.example` 的 `AGENT_SERVER_HOST / PORT / TOKEN` 配置。

> 本项目 `coop/` 为脚手架阶段：结构清晰、职责明确、协议清晰，可后续填充真实实现。
