# 24 · 服务 API 与错误码（对外契约 + make 目标规格）

> 两个契约：**HTTP API**（api/worker/控制台共用）与 **make 目标**（人机共用的入口）。
> 两者都进版本控制，都是"接口"，都不许随手改。
>
> **v3 变更（重要）**：
> 1. 端点不再以路径登记，而是以 **`op_id`（操作符号）** 登记，路径由 `config/bindings/service.yaml` 提供。
>    理由：服务端路由写进代码/文档，改版就是全仓 grep——与 `27` 的铁律同源。
> 2. 新增路由相关错误码（`VP-1005/1006`、`VP-2006`、`VP-5005/5006`）。
> 3. make 目标新增 `lint-noroute` / `routes` / `record-routes` / `rediscovery-drill`。

## 1. HTTP API 总则

| 项 | 规格 |
|---|---|
| 版本 | 破坏性变更才升大版本（`v1` → `v2`）；**版本前缀由 `bindings` 的 `api_version` 提供，代码与文档内不写前缀** |
| 编码 | JSON，`Content-Type: application/json; charset=utf-8` |
| 认证 | `X-Veripen-Token: <token>`（token 来自 `.env`，不进 Git）；控制台用同一 token 的只读子集 |
| 幂等 | `tasks.submit` 靠 `idem_key` 去重；重复提交返回 **200 + 已有对象**（不是 409） |
| 分页 | `?limit=50&cursor=<opaque>`；返回 `{items, next_cursor}`；`limit` 上限 200 |
| 时间 | 全部 UTC RFC3339；展示层才转本地 |
| 错误 | 统一信封（见 §4），HTTP 状态码 + `code` 双轨 |
| 版本头 | 响应带 `X-Veripen-Version` 与 `X-Veripen-Profile-Hash`（涉及时） |
| 速率 | API 自身 60 req/s（本地工具用，不做复杂限流） |

## 2. 操作表（`op_id`，不是路径）

> 下表是**符号表**。`op_id` 在代码里以常量引用（`OP.TASK.SUBMIT` 等），
> 客户端调 `client.call(OP.TASK.SUBMIT, payload)`。
> 具体传输（方法/路径）只在 `config/bindings/service.yaml` 里，部署时可覆盖。

### 2.1 任务

| op_id | 符号常量 | 说明 | 关键字段 |
|---|---|---|---|
| `task.submit` | `OP.TASK.SUBMIT` | 建任务（幂等） | 请求：`{engagement_id, profile, asset_ids[], oracle_ids[], params?}`<br>响应：`{task_id, state, idem_key, reused:bool}` |
| `task.get` | `OP.TASK.GET` | 单任务 | `state, attempt, lease_until, budget, routeset_sha256, result` |
| `task.list` | `OP.TASK.LIST` | 列表 | `?engagement_id=&state=&limit=&cursor=` |
| `task.cancel` | `OP.TASK.CANCEL` | 取消 | 仅 `QUEUED/LEASED/RUNNING` 可取消 |
| `task.requeue` | `OP.TASK.REQUEUE` | 死信重排 | 仅 `DEAD_LETTER`；`attempt` 归零并记事件 |

### 2.2 发现与证据

| op_id | 说明 |
|---|---|
| `finding.list` | `?engagement_id=&state=&severity=&oracle_id=&route_strength=&limit=&cursor=` |
| `finding.get` | 含 `capsule_id`、`assertion`、`replay_status`、`route` 摘要 |
| `finding.feedback` | 人工判定 `{verdict: true_positive\|false_positive\|unreproducible, reason_code, note}` |
| `capsule.get` | 元数据（不含正文） |
| `capsule.download` | 脱敏后的 capsule JSON（`Content-Disposition`） |
| `capsule.replay` | 触发 replay，返回**七态之一**（含两种路由态，见 `06 §5.1`） |

### 2.3 路由（v3 新增）

| op_id | 说明 |
|---|---|
| `route.discover` | 对某资产触发一次 `route_discovery`，返回 `{routeset_sha256, added, removed, changed, unresolved[]}`；**不返回模板正文** |
| `route.list` | 列出某 `routeset_sha256` 的绑定摘要 `[{ref, tags, confidence, source, ttl_s}]` |
| `route.explain` | 给定 `ref`，返回全部 provenance + `discovery_evidence_ids` + 置信度计算过程（审计第四问，`05 §6`） |
| `route.diff` | 两个 `routeset_sha256` 的结构差异（用于"契约改版影响面"评估） |

### 2.4 审批

| op_id | 说明 |
|---|---|
| `approval.list` | `?state=pending`；含"影响说明"结构化字段 |
| `approval.decide` | `{decision: approved\|rejected, reason, by}`；`by != lease_owner` 强校验 |

### 2.5 观测与运维

| op_id | 说明 |
|---|---|
| `stats.task` | 四表数据（步耗时 / token 分布 / 重试降级 / 重发现轨迹） |
| `stats.engagement` | 聚合指标（请求数、QPS 峰值、发现数、成本、解析率） |
| `coverage` | `?engagement_id=` 资产 × oracle 矩阵 **+ 标签 × oracle 矩阵** |
| `bounds` | 越界事件（正常应为空数组） |
| `healthz` | 进程活着（不查依赖） |
| `readyz` | 依赖就绪（Redis/DB/模型/契约可达，等价 doctor 的机器版） |

### 2.6 控制台（只读）

| op_id | 说明 |
|---|---|
| `ui.overview` | 概览（engagements 列表 + 统计） |
| `ui.findings` | 发现表（排序/筛选在前端做） |
| `ui.finding` | 详情（assertion + trace 摘要 + 下载） |
| `ui.unverified` | 未能验证单列（含 `SKIPPED` 两类分列） |
| `ui.coverage` | 覆盖矩阵（含 0 命中标签） |
| `ui.routes` | **绑定集浏览（只读，摘要视图，无模板）** |
| `ui.audit` | 留痕 |
| `ui.bounds` | 越界页（非空时页头红色） |

**只读强制**：`ui.*` 下的写操作返回 `405`。写操作只能走非 `ui.*` 的 op 且需 token。
B15 的 DoD 里有一条：对 `ui.*` 任一 op 发写请求 → 405。

## 3. 绑定契约（`config/bindings/service.yaml`）

```yaml
# 传输层配置：代码内无默认值，缺失即启动失败（fail-closed）
version: 1
ops:
  task.submit:      {transport: http, method: POST,   path_key: OPS_TASK_SUBMIT}
  task.get:         {transport: http, method: GET,    path_key: OPS_TASK_GET}
  capsule.replay:   {transport: http, method: POST,   path_key: OPS_CAPSULE_REPLAY}
  route.discover:   {transport: http, method: POST,   path_key: OPS_ROUTE_DISCOVER}
  route.explain:    {transport: http, method: GET,    path_key: OPS_ROUTE_EXPLAIN}
  # … 其余同构
```

`path_key` 指向 `.env` 变量名，**值由部署注入**。

**为什么用 `path_key` 而不是直接写路径**：这样 `service.yaml` 本身也不含地址，
可以进 git、可以给客户看、可以被 `lint-noroute` 通过。真正的地址只在部署环境的 `.env` 里。

**验收入口**：`veripen bindings check` 校验每个 `op_id` 都有绑定、每个 `path_key` 都有值。

## 4. 错误码目录（41 个，唯一清单，代码里只许引用这里的常量）

信封：

```json
{"error": {"code": "VP-2001", "message": "asset not in scope",
           "retryable": false, "detail": {"asset_id": "web-09"},
           "hint": "把 web-09 加入 profiles/demo-a/scope.yaml 的 assets，或从请求里移除"}}
```

| 段 | 前缀 | 领域 |
|---|---|---|
| 1xxx | `VP-1` | 配置 / profile / schema / **路由配置** |
| 2xxx | `VP-2` | 授权 / scope / 越界 / **绑定** |
| 3xxx | `VP-3` | 调度 / 租约 / 预算 |
| 4xxx | `VP-4` | oracle / 判定 / 靶场 |
| 5xxx | `VP-5` | 证据 / capsule / replay / **路由证据** |
| 6xxx | `VP-6` | 报告 / 渲染 / 导出 |
| 7xxx | `VP-7` | 交付 / 离线包 / 迁移 |
| 8xxx | `VP-8` | 模型 / 网关 / 降级 |

| code | 含义 | retryable | 处理 |
|---|---|---|---|
| VP-1001 | profile schema 校验失败 | ❌ | 启动即失败，指出字段路径 |
| VP-1002 | profile 语义校验失败（scope 空 / deny 与 assets 交集 / 缺 secondary） | ❌ | 启动即失败 |
| VP-1003 | secret_ref 无法解析 | ❌ | 启动即失败，提示 `make vault-put` |
| VP-1004 | 测试窗口无效 | ❌ | 拒绝启动 |
| **VP-1005** | **profile 内含可网络定位字面量** | ❌ | 启动即失败，指出行号（`lint-noroute` 同源） |
| **VP-1006** | **契约自述 host 不在 assets 解析集合** | ❌ | 拒绝启动；这是 scope 越界的前兆 |
| VP-2001 | asset 不在 scope | ❌ | BLOCKED，不发包 |
| VP-2002 | 目标 IP 在 deny 内（post_connect 命中） | ❌ | 断连 + 越界事件 + 整任务停 |
| VP-2003 | 跳转目标越界 | ❌ | 不跟随，记 BLOCKED |
| VP-2004 | impact 超过 `max_impact_class` | ❌ | 需审批或拒绝 |
| VP-2005 | 无审批门却请求高危动作 | ❌ | 直接拒绝（不是"等待审批"） |
| **VP-2006** | **未绑定/绑定过弱/绑定过期** | 部分 ✅（过期可重发现） | `UNBOUND_ROUTE` 抛错（bug）；`RouteTooWeak`→BLOCKED；`RouteStale`→重发现 |
| **VP-2007** | **来源越界：请求的 ref 不在当前 RouteSet** | ❌ | 抛错 + 记 `binding ok=0 UNBOUND` + 整任务停（`05 §1.0.2`） |
| VP-3001 | 租约丢失（被回收） | ✅ 由调度重排 | 当前 worker 自杀式退出 |
| VP-3002 | 预算耗尽 | ❌ | 收敛并标 `budget_exhausted` |
| VP-3003 | 限流排队超时 | ✅ | 该步 INCONCLUSIVE，任务继续 |
| VP-3004 | 目标连续 5xx 熔断 | ✅ | 该资产熔断 5min |
| **VP-3005** | **重发现超配额** | ❌ | 停止重发现，剩余标 `BLOCKED(route_unresolved)`，标 `budget_exhausted: rediscovery` |
| VP-4001 | oracle 不适用于该资产 | ❌ | REFUTED 或跳过 |
| VP-4002 | 缺少前置（如无 secondary 账号） | ❌ | BLOCKED |
| VP-4003 | 靶场未就绪 | ✅ | 等就绪或 `make up` |
| VP-4004 | 判定材料不足（对照缺失/环境抖动） | ✅ 有限次 | INCONCLUSIVE，不入报告正文 |
| **VP-4005** | **无标签命中（该资产无此测试面）** | ❌ | `SKIPPED(no_selector_match)`，进"未覆盖" |
| VP-5001 | capsule 哈希不符（篡改） | ❌ | 拒绝 replay，报告失败 |
| VP-5002 | capsule 缺失 | ❌ | 渲染期抛错 |
| VP-5003 | replay 被 scope 拒绝 | ❌ | `REPLAY_BLOCKED`（安全行为） |
| VP-5004 | replay 判定未复现 | ⚠️ 人工 | `ASSERTION_NOT_REPRODUCED` |
| **VP-5005** | **replay 时该路由在当前环境无法解析** | ⚠️ 人工 | `REPLAY_ROUTE_UNRESOLVED`（**不等于已修复**） |
| **VP-5006** | **replay 时路由结构漂移** | ⚠️ 人工 | `REPLAY_ROUTE_DRIFT`，需确认后重判 |
| VP-6001 | 报告渲染缺 capsule | ❌ | 抛错，不降级 |
| **VP-6002** | **报告含可网络定位字面量** | ❌ | 拒绝出包（`lint-noroute-report`） |
| VP-6003 | SARIF schema 校验失败 | ❌ | 拒绝出包 |
| VP-6004 | manifest 校验失败 | ❌ | 拒绝交付 |
| **VP-6005** | **弱绑定被标为高严重度** | ❌ | 渲染期抛错（`09 §3.1`） |
| VP-7001 | 离线包校验和失败 | ❌ | 停止安装 |
| VP-7002 | 迁移版本不匹配 | ❌ | 提示 `make migrate` |
| **VP-7003** | **bindings 缺失或 path_key 无值** | ❌ | 启动即失败（`bindings check`） |
| VP-8001 | 模型不可达 | ✅ | 降级链 → scripted planner |
| VP-8002 | 模型输出非结构化 | ✅ 1 次修复 | 再失败降级 scripted planner |
| VP-8003 | 模型超时 | ✅ | 计入 wall_clock，降级 |
| **VP-8004** | **模型试图指定地址（越权请求）** | ❌ | 记 `adversarial_prompt_rejected`，不是安全事件，但计入 stats |

**规则**：新增错误码只能追加，不许改义；`retryable` 决定是否进重试路径（对齐 `04 §3` 的四分类）。

> 注：VP-5005 与 VP-5006 的 `retryable=⚠️ 人工` 是刻意的——
> 它们既不能自动重试（重试也没用），也不该算失败（可能是环境变化）。
> 把它们塞进"失败"或"成功"任何一栏，都会产生错误结论。

## 5. make 目标规格（38 个目标，每个 = 一条命令 + 一句断言）

| 目标 | 实际执行 | 断言什么 |
|---|---|---|
| `make up` | `docker compose up -d --wait` | 全部 healthcheck 通过 |
| `make down` | `docker compose down -v` | 清理干净 |
| `make demo` | `scripts/demo.sh demo-a` | 出 ≥3 发现，全部 replay OK |
| `make demo-alt` | `scripts/demo.sh demo-b` | 同镜像换 profile 跑通 |
| `make demo-offline` | `scripts/demo.sh --offline-fixture` | 断网/无模型也能出报告 |
| `make replay ID=n` | `veripen replay --finding n` | 七态之一 |
| `make bench [MODEL=x]` | `veripen bench --rounds 3` | FP=0、越界=0、verdict 一致、**routeset 三轮一致** |
| `make cases CASES=f` | `veripen cases --from f --out cases.yaml` | 两次生成 `routeset_sha256` 一致 |
| `make cases-diff` | `veripen cases --diff` | 显示新增/删除/标签变更条数 |
| `make coverage-matrix` | `veripen coverage --matrix` | 14 类 × 资产 + **标签 × oracle** 矩阵无空格（或有原因） |
| `make doctor [--deep]` | `veripen doctor` | 16 项全绿（含 bindings check、路由卫生 5 条） |
| `make package-offline` | `scripts/package-offline.sh` | 产出 tar + SHA256SUMS，校验通过 |
| **`make lint-noroute`** | `node scripts/lint/check-noroute.cjs` | 全仓 0 处路径/URL/host/端口字面量（例外 ≤6 行且带理由） |
| **`make lint-noroute-report`** | `veripen report --lint-noroute` | 报告三视图 0 处可定位字面量 |
| `make lint-noleak` | `scripts/lint-noleak.sh` | core 内无客户标识 |
| `make lint-report` | `veripen report --lint` | 措辞合规 + 三视图同源 + 弱绑定不升级 |
| `make verify-chain` | `veripen evidence --verify-chain` | 哈希链完整 |
| `make verify-manifest` | `veripen delivery --verify` | 交付封条完整 |
| `make verify-capsule` | `veripen evidence --schema` | capsule v1.1 必含 `route` 块 |
| **`make routes ASSET=y`** | `veripen routes discover --asset y` | 解析成功且打印摘要（无地址） |
| **`make routes-explain REF=r`** | `veripen routes explain --ref r` | 输出完整 provenance（审计第四问） |
| **`make routes-diff A=a B=b`** | `veripen routes diff` | 结构差异可读 |
| **`make record-routes ASSET=y`** | `veripen routes record` | 从真实解析录制 L0 夹具，写 sha |
| **`make rediscovery-drill`** | `scripts/drill/rediscovery.sh` | 删一条绑定后：有 `rediscovery` 事件、无兜底、结论正确 |
| **`make bindings-check`** | `veripen bindings check` | 每个 op_id 有绑定、每个 path_key 有值 |
| `make stats TASK=x` | `veripen stats --task x` | 四表输出（含重发现轨迹） |
| `make requeue ID=n` | `veripen tasks --requeue n` | 死信重排成功 |
| `make migrate` | `veripen db migrate` | schema 版本对齐 |
| `make vault-rotate` | `veripen vault --rotate` | 新密钥可用，旧 capsule 仍可读 |
| `make canary-off` | `veripen workers --canary off` | canary worker 停止领取 |
| `make range-selftest` | `pytest tests/range` | 靶场按契约工作、10 缺陷 + 10 安全孪生成立（见 25 §6） |
| `make data-verify` | `veripen data --verify` | 每份 `data/` 的 manifest 合法、sha256/行数/字段/`pii:false` 全对 |
| `make data-fetch` | `scripts/data/fetch.sh` | **维护者机器专用**：更新快照并写 manifest；运行时绝不联网 |
| `make record ORACLE=x ASSET=y` | `veripen fixtures --record` | 从真实靶场录夹具，写 `tests/fixtures/` + 镜像 digest |
| `make lint-nodata-stub` | `scripts/lint/nodata_stub.sh` | 全仓 0 命中 mock/stub/fake/dummy、0 处硬编码 <5 行样本 |
| `make test` | `lint + routes(L0) + unit(L1) + oracle(L2) + report-lint` | 全绿 |
| `make test-fast` | `lint + routes + unit` | 秒级 |
| `make test-e2e` | `compose up + demo + demo-alt + replay + rediscovery-drill + down` | 全绿 |

**规则**：任何 `make` 目标都必须能独立跑（不依赖前一条命令的副作用）；不能跑的目标不许进 Makefile。

## 6. 兼容性承诺

| 对象 | 承诺 |
|---|---|
| `op_id` | 只加不删；删 op 必须升大版本 |
| 字段 | 只加不删；删字段必须升大版本 |
| 错误码 | 只追加；`retryable` 语义不变 |
| capsule schema | `capsule_version` 升位；旧版永久可读（1.0 无 `route` 块仍可渲染，但标注"无路由证据"） |
| profile schema | `apiVersion` 升位 + 提供 `veripen profile migrate` |
| **RouteSet schema** | `resolver_version` 升位；旧 routeset 永久可读（replay 用旧版本 resolver） |
| make 目标 | 只加不删（脚本和文档都引用它们） |

> `RouteSet` 的兼容承诺是必须的：证据要能 replay 三年后，
> 所以 resolver 的旧版本必须能重新装出来（镜像里保留 `resolver_version` 分发）。
