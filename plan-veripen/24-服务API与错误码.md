# 24 · 服务 API 与错误码（对外契约 + make 目标规格）

> 两个契约：**HTTP API**（api/worker/控制台共用）与 **make 目标**（人机共用的入口）。
> 两者都进版本控制，都是"接口"，都不许随手改。

## 1. HTTP API 总则

| 项 | 规格 |
|---|---|
| 前缀 | `/v1`（破坏性变更才升 `/v2`，字段只加不删） |
| 编码 | JSON，`Content-Type: application/json; charset=utf-8` |
| 认证 | `X-Veripen-Token: <token>`（token 来自 `.env`，不进 Git）；控制台用同一 token 的只读子集 |
| 幂等 | `POST /v1/tasks` 靠 `idem_key` 去重；重复提交返回 **200 + 已有对象**（不是 409） |
| 分页 | `?limit=50&cursor=<opaque>`；返回 `{items, next_cursor}`；`limit` 上限 200 |
| 时间 | 全部 UTC RFC3339（`2026-11-01T02:14:03Z`）；展示层才转本地 |
| 错误 | 统一信封（见 §4），HTTP 状态码 + `code` 双轨 |
| 版本头 | 响应带 `X-Veripen-Version: <semver>` 与 `X-Veripen-Profile-Hash`（涉及时） |
| 速率 | API 自身 60 req/s（本地工具用，不做复杂限流） |

## 2. 端点全表

### 2.1 任务

| 方法 | 路径 | 说明 | 关键字段 |
|---|---|---|---|
| POST | `/v1/tasks` | 建任务（幂等） | 请求：`{engagement_id, profile, asset_ids[], oracle_ids[], params?}`<br>响应：`{task_id, state, idem_key, reused:bool}` |
| GET | `/v1/tasks/{id}` | 单任务 | `state, attempt, lease_until, budget, result` |
| GET | `/v1/tasks` | 列表 | `?engagement_id=&state=&limit=&cursor=` |
| POST | `/v1/tasks/{id}/cancel` | 取消 | 仅 `QUEUED/LEASED/RUNNING` 可取消；返回新状态 |
| POST | `/v1/tasks/{id}/requeue` | 死信重排 | 仅 `DEAD_LETTER`；`attempt` 归零并记事件 |

### 2.2 发现与证据

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/v1/findings` | `?engagement_id=&state=&severity=&oracle_id=&limit=&cursor=` |
| GET | `/v1/findings/{id}` | 含 `capsule_id`、`assertion`、`replay_status` |
| POST | `/v1/findings/{id}/feedback` | 人工判定 `{verdict: true_positive\|false_positive\|unreproducible, reason_code, note}` |
| GET | `/v1/capsules/{id}` | 元数据（不含正文） |
| GET | `/v1/capsules/{id}/download` | 脱敏后的 capsule JSON（`Content-Disposition`） |
| POST | `/v1/capsules/{id}/replay` | 触发 replay，返回四态之一（异步则返回 `replay_job_id`） |

### 2.3 审批

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/v1/approvals` | `?state=pending`；含"影响说明"结构化字段 |
| POST | `/v1/approvals/{id}` | `{decision: approved\|rejected, reason, by}`；`by != lease_owner` 强校验 |

### 2.4 观测与运维

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/v1/stats/task/{id}` | 三表数据（步耗时 / token 分布 / 重试降级） |
| GET | `/v1/stats/engagement/{id}` | 聚合指标（请求数、QPS 峰值、发现数、成本） |
| GET | `/v1/coverage` | `?engagement_id=` 资产 × oracle 矩阵 |
| GET | `/v1/bounds` | 越界事件（正常应为空数组） |
| GET | `/v1/healthz` | 进程活着（不查依赖） |
| GET | `/v1/readyz` | 依赖就绪（Redis/DB/模型/靶场，等价 doctor 的机器版） |

### 2.5 控制台（只读）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/ui/` | 概览（engagements 列表 + 统计） |
| GET | `/ui/e/{eng}/findings` | 发现表（排序/筛选在前端做） |
| GET | `/ui/e/{eng}/f/{id}` | 详情（assertion + trace 摘要 + 下载） |
| GET | `/ui/e/{eng}/unverified` | 未能验证单列 |
| GET | `/ui/e/{eng}/coverage` | 覆盖矩阵 |
| GET | `/ui/e/{eng}/audit` | 留痕 |
| GET | `/ui/bounds` | 越界页（非空时页头红色） |

**只读强制**：`/ui/*` 下所有非 GET 请求返回 `405`；写操作只能走 `/v1/*` 且需 token。B15 的 DoD 里有一条 `curl -X POST /ui/... → 405`。

## 3. 请求/响应示例（幂等提交）

```http
POST /v1/tasks
X-Veripen-Token: ***
Content-Type: application/json

{
  "engagement_id": "ENG-2026-001",
  "profile": "demo-a",
  "asset_ids": ["web-01"],
  "oracle_ids": ["sqli_bool_diff", "idor_cross_account"]
}
```

```json
200 OK
{
  "task_id": "01JAB7K3Q9",
  "state": "QUEUED",
  "idem_key": "sha256:5f1c…",
  "reused": false,
  "created_at": "2026-11-01T02:14:03Z"
}
```

第二次同参数提交 → `"reused": true`，`task_id` 相同，**不再发任何请求**。

## 4. 错误码目录（唯一清单，代码里只许引用这里的常量）

信封：

```json
{"error": {"code": "VP-2001", "message": "asset not in scope",
           "retryable": false, "detail": {"asset_id": "web-09"},
           "hint": "把 web-09 加入 profiles/demo-a/scope.yaml 的 assets，或从请求里移除"}}
```

| 段 | 前缀 | 领域 |
|---|---|---|
| 1xxx | `VP-1` | 配置 / profile / schema |
| 2xxx | `VP-2` | 授权 / scope / 越界 |
| 3xxx | `VP-3` | 调度 / 租约 / 预算 |
| 4xxx | `VP-4` | oracle / 判定 / 靶场 |
| 5xxx | `VP-5` | 证据 / capsule / replay |
| 6xxx | `VP-6` | 报告 / 渲染 / 导出 |
| 7xxx | `VP-7` | 交付 / 离线包 / 迁移 |
| 8xxx | `VP-8` | 模型 / 网关 / 降级 |

| code | 含义 | retryable | 处理 |
|---|---|---|---|
| VP-1001 | profile schema 校验失败 | ❌ | 启动即失败，指出字段路径 |
| VP-1002 | profile 语义校验失败（scope 空 / deny 与 assets 交集 / 缺 secondary） | ❌ | 启动即失败 |
| VP-1003 | secret_ref 无法解析 | ❌ | 启动即失败，提示 `make vault-put` |
| VP-1004 | 测试窗口无效 | ❌ | 拒绝启动 |
| VP-2001 | asset 不在 scope | ❌ | BLOCKED，不发包 |
| VP-2002 | 目标 IP 在 deny 内（post_connect 命中） | ❌ | 断连 + 越界事件 + 整任务停 |
| VP-2003 | 跳转目标越界 | ❌ | 不跟随，记 BLOCKED |
| VP-2004 | impact 超过 `max_impact_class` | ❌ | 需审批或拒绝 |
| VP-2005 | 无审批门却请求高危动作 | ❌ | 直接拒绝（不是"等待审批"） |
| VP-3001 | 租约丢失（被回收） | ✅ 由调度重排 | 当前 worker 自杀式退出 |
| VP-3002 | 预算耗尽 | ❌ | 收敛并标 `budget_exhausted` |
| VP-3003 | 限流排队超时 | ✅ | 该步 INCONCLUSIVE，任务继续 |
| VP-3004 | 目标连续 5xx 熔断 | ✅ | 该资产熔断 5min |
| VP-4001 | oracle 不适用于该资产 | ❌ | REFUTED 或跳过 |
| VP-4002 | 缺少前置（如无 secondary 账号） | ❌ | BLOCKED |
| VP-4003 | 靶场未就绪 | ✅ | 等 healthcheck 或 `make up` |
| VP-4004 | 判定材料不足（对照缺失/环境抖动） | ✅ 有限次 | INCONCLUSIVE，不入报告正文 |
| VP-5001 | capsule 哈希不符（篡改） | ❌ | 拒绝 replay，报告失败 |
| VP-5002 | capsule 缺失 | ❌ | 渲染期抛错 |
| VP-5003 | replay 被 scope 拒绝 | ❌ | `REPLAY_BLOCKED`（安全行为） |
| VP-5004 | replay 判定未复现 | ⚠️ 人工 | `ASSERTION_NOT_REPRODUCED` |
| VP-6001 | 报告渲染缺 capsule | ❌ | 抛错，不降级 |
| VP-6002 | SARIF schema 校验失败 | ❌ | 拒绝出包 |
| VP-6003 | manifest 校验失败 | ❌ | 拒绝交付 |
| VP-7001 | 离线包校验和失败 | ❌ | 停止安装 |
| VP-7002 | 迁移版本不匹配 | ❌ | 提示 `make migrate` |
| VP-8001 | 模型不可达 | ✅ | 降级链 → scripted planner |
| VP-8002 | 模型输出非结构化 | ✅ 1 次修复 | 再失败降级 scripted planner |
| VP-8003 | 模型超时 | ✅ | 计入 wall_clock，降级 |

**规则**：新增错误码只能追加，不许改义；`retryable` 决定是否进重试路径（对齐 `04 §3` 的三分类）。

## 5. make 目标规格（每个目标 = 一条命令 + 一句断言）

| 目标 | 实际执行 | 断言什么 |
|---|---|---|
| `make up` | `docker compose up -d --wait` | 全部 healthcheck 通过 |
| `make down` | `docker compose down -v` | 清理干净 |
| `make demo` | `scripts/demo.sh demo-a` | 出 ≥3 发现，全部 replay OK |
| `make demo-alt` | `scripts/demo.sh demo-b` | 同镜像换 profile 跑通 |
| `make demo-offline` | `scripts/demo.sh --offline-fixture` | 断网/无模型也能出报告 |
| `make replay ID=n` | `veripen replay --finding n` | 四态之一 |
| `make bench [MODEL=x]` | `veripen bench --rounds 3` | FP=0、越界=0、verdict 一致 |
| `make cases CASES=f` | `veripen cases --from f --out cases.yaml` | 两次生成字节一致 |
| `make cases-diff` | `veripen cases --diff` | 显示新增/删除条数 |
| `make coverage-matrix` | `veripen coverage --matrix` | 14 类 × 资产矩阵无空格（或有原因） |
| `make doctor [--deep]` | `veripen doctor` | 15 项全绿 |
| `make package-offline` | `scripts/package-offline.sh` | 产出 tar + SHA256SUMS，校验通过 |
| `make lint-noleak` | `scripts/lint-noleak.sh` | core 内无客户标识 |
| `make lint-report` | `veripen report --lint` | 措辞合规 + 三视图同源 |
| `make verify-chain` | `veripen evidence --verify-chain` | 哈希链完整 |
| `make verify-manifest` | `veripen delivery --verify` | 交付封条完整 |
| `make stats TASK=x` | `veripen stats --task x` | 三表输出 |
| `make requeue ID=n` | `veripen tasks --requeue n` | 死信重排成功 |
| `make migrate` | `veripen db migrate` | schema 版本对齐 |
| `make vault-rotate` | `veripen vault --rotate` | 新密钥可用，旧 capsule 仍可读 |
| `make canary-off` | `veripen workers --canary off` | canary worker 停止领取 |
| `make range-selftest` | `pytest tests/range` | 靶场按契约工作、10 缺陷 + 10 安全孪生成立（见 25 §6） |
| `make data-verify` | `veripen data --verify` | 每份 `data/` 的 manifest 合法、sha256/行数/字段/`pii:false` 全对（见 26） |
| `make data-fetch` | `scripts/data/fetch.sh` | **维护者机器专用**：更新快照并写 manifest；运行时绝不联网 |
| `make record ORACLE=x ASSET=y` | `veripen fixtures --record` | 从真实靶场录夹具，写 `tests/fixtures/` + 镜像 digest |
| `make lint-nodata-stub` | `scripts/lint/nodata_stub.sh` | 全仓 0 命中 mock/stub/fake/dummy、0 处硬编码 <5 行样本 |
| `make test` | `lint + unit + oracle + report-lint` | 全绿 |
| `make test-fast` | `lint + unit` | 秒级 |
| `make test-e2e` | `compose up + demo + demo-alt + replay + down` | 全绿 |

**规则**：任何 `make` 目标都必须能独立跑（不依赖前一条命令的副作用）；不能跑的目标不许进 Makefile。

## 6. 兼容性承诺

| 对象 | 承诺 |
|---|---|
| `/v1` 字段 | 只加不删；删字段必须 `/v2` |
| 错误码 | 只追加；`retryable` 语义不变 |
| capsule schema | `capsule_version` 升位；旧版永久可读 |
| profile schema | `apiVersion` 升位 + 提供 `veripen profile migrate` |
| make 目标 | 只加不删（脚本和文档都引用它们） |
