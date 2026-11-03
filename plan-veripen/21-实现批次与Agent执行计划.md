# 21 · 实现批次与 Agent 执行计划

> 本文回答一个问题：**既然 agent 写代码很快，为什么还要分批？**
>
> 因为瓶颈已经从"写代码"转移到 **"验证 + 集成"**。一次性给 agent 20 篇规格，它会在两小时内产出一个
> 4,000 行、跑不起来、也说不清哪里坏了的东西——**代码量不是成本，无法定位的失败才是成本**。
>
> **v3 变更**：新增 **B1.5（Route Layer）** 作为独立批次，编号刻意用 1.5 而不是 18——
> 它的位置（B1 之后、B2 之前）比它的序号更重要。
> 理由：如果 oracle 先写完、路由后补，所有 oracle 都会长成"接受 URL"的形态，再改就是重写。
> 路由层必须先立，oracle 才会天然写成"只见绑定"的样子。

## 1. 总览：8 波 30 批

```
W1 内核骨架    B1 骨架+契约  B1.5 路由层  B2 调度  B3 guard  B4 证据
W2 能力扩容    B5 TierA×4   B6 TierA×2  B7 TierB×5  B8 契约解析  B9 认证清单
W3 工程交付    B10 agent循环 B11 报告三视图 B12 bench  →（B12.1–B12.13 补齐）→ B13 doctor+离线包+3worker
W3.6 补齐扩容  B12.1 黑板 B12.2 outcome B12.3 workflow B12.4 knowledge B12.5 多agent
W3.7 能打能证  B12.6 act.* 自由动作 B12.7 线索闭环 B12.8 操作台/L0-TUI
W3.8 能力市场  B12.10 发现 B12.11 注册 B12.12 装配准入 B12.13 插件UI
W3.9 通用工作台 B12.14 fs.* B12.15 shell+代理 B12.16 plan/todo
W4 平台化      B14 TierC×3  B15 Web控制台  B16 demo-b档  B17 交付物料
```

| 波 | 批次 | 退出标准（整波） | 累计断言 |
|---|---|---|---|
| W1 | B1, B1.5, B2–B4 | 1 个 oracle 端到端跑通 + 证据可 replay + **零地址字面量** | 冷启动门 + A9 |
| W2 | B5–B9 | 14 类里 11 类可跑；契约解析出 200 条绑定 | A6 + A7 |
| W3 | B10–B13 | 三视图 + 44 例 bench + 断网可装 | A3/A4/A5/A8 |
| W4 | B14–B17 | 14 类全绿 + 双档 + 控制台 + 交付物料 | A1/A2/A6/A7 |

**为什么 B1.5 插在 B2 之前而不是之后**：
`guard.scope`（B3）的四处校验里有"绑定门"，`evidence`（B4）的 capsule 要含 `route` 块。
这两批都依赖路由层的类型定义。放在 B2 之前，是为了让类型先冻结。

## 2. 批次卡（每批 = 一次 agent 运行）

### W1 · 内核骨架

| # | 批次 | 目标 | 依赖 | 产出（允许改的路径） | DoD（跑这条命令必须过） |
|---|---|---|---|---|---|
| **B1** | 骨架与契约 | **九契约测试**（v3.1 加 `test_asset_facts_append_only_and_sourced`；v3.5 加 `test_workbench_cannot_import_verdict` 与 `test_shell_network_goes_through_proxy`）+ models + 3 表 + api | — | `pyproject.toml` `docker-compose.yml` `veripen_core/{models,store}.py` `api/` `config/bindings/service.yaml` `tests/unit/test_contracts.py` | `pytest tests/unit/test_contracts.py` **9 绿**；`make bindings-check` 通过；两次同 idem_key 同 id；`make lint-noroute` 0 命中；`make lint-nodata-stub` 0 命中；**`asset_facts` 表存在且 UPDATE/DELETE 语句 0 条**（`01 §7` 第七条） |
| **B1.5** | **路由层** | `RouteRef/RouteBinding/RouteSet` 类型 + resolver + merge（structural_key 五维）+ 置信函数 + cache/single-flight + `contract`/`observed` 两源 | B1 | `veripen_core/routes/**` `tests/routes/**` `tests/routes/fixtures/**` | **L0-01…L0-35 共 35 条逐条通过**（`11 §2`）；`pytest -k routes_contract` 3 绿；同查询两次同 `routeset_sha256`；`confidence` 无字面赋值（L0-18） |
| **B2** | 调度 | 租约/心跳/回收/transition | B1 | `veripen_core/{lease,scheduler}.py` `worker/` | `kill -9` worker 后 35s 内任务被接管；`transition` 并发 2 线程只 1 成功 |
| **B3** | guard | scope **四处** + 令牌桶 + 审批骨架 + `discovery_probe` 门 | B1.5 | `veripen_core/guard/` `tests/unit/test_guard.py` | 缩 scope 后拒绝且零请求；**未绑定/弱绑定/过期绑定三种情况各自拒绝**；令牌桶 100 并发≈20/s；越界记 `scope ok=0` |
| **B4** | 证据 | capsule **1.1**（含 route 块）/chain/replay **六态** | B1.5 | `veripen_core/evidence.py` `veripen_cli/cmd_replay.py` | 同输入两次 seal 同哈希；改一字节报 TAMPERED；`path_rendered` 改动不影响 `self_sha256`；`--offline-fixture` 断网可跑；**删绑定→`REPLAY_ROUTE_UNRESOLVED`；改模板→`REPLAY_ROUTE_DRIFT`** |

### W2 · 能力扩容

| # | 批次 | 目标 | 依赖 | 产出 | DoD |
|---|---|---|---|---|---|
| **B5** | TierA 前 4 类 | sqli_bool_diff / idor_cross_account / ssrf_oob / jwt_replay + OOB listener | B4 | `veripen_oracles/{4 个}/` `oob/` `config/bindings/sources.yaml`(oob_ref) | 靶场 4 类各 ≥1 VERIFIED；负样本全 REFUTED；单测 ≥4正+2负/类（夹具来自 `make record`）；`git diff --stat veripen_core/` 为空 |
| **B6** | TierA 余 2 类 | open_redirect / path_traversal | B5 | `veripen_oracles/{2 个}/` | 各 1 VERIFIED + 2 REFUTED；穿越深度由绑定计算（无常量）；指纹库命中可解释 |
| **B7** | TierB 5 类 | sqli_error / bola_functional / xss_reflected / mass_assignment / rate_limit | B5 | `veripen_oracles/{5 个}/` | mass/rate 默认 `enabled:false` 且启动校验生效；xss 三上下文各自判定；bola 用 `admin_function`+`normal_function` 双标签 |
| **B8** | 契约解析 | parser + tagger + planner → `RouteSet`（**不产路径**） | B1.5, B6 | `veripen_contract/` `veripen_cli/cmd_cases.py` | 40 op → 200 条绑定；两次 `routeset_sha256` 一致；契约 host 越界启动即拒（VP-1006）；`grep -rE "httpx\|requests" veripen_contract/` 为空；未登记标签报错 |
| **B9** | 认证清单 | JWT J5–J10 变体 + OAuth/mTLS/session 半自动脚本 | B7 | `veripen_oracles/jwt_replay/` 扩展 `checks/auth/` | J1–J10 全部有对应用例；OAuth 8 项有脚本；覆盖矩阵落报告（含 0 命中标签） |

### W3 · 工程与交付

| # | 批次 | 目标 | 依赖 | 产出 | DoD |
|---|---|---|---|---|---|
| **B10** | agent 循环 | 五预算/工具 schema（含 `list_routes`/`rediscover_routes`）/降级链/上下文四层/step.jsonl | B7, B8 | `veripen_core/{budget,context}.py` `agent/` `tools/` | `max_steps=5` 正常收敛并标 budget_exhausted；模型名写错→降级 scripted planner 仍完成；非法 JSON 回喂 ≤3 次；**给模型"直接访问某地址"的诱导 → 无法执行**；`max_rediscovery=0` 仍能完成；**单轮多工具调用被拒（`tool_calls_per_step=1`）；退化生成 1000 次调用时在 200 处硬停**；**工具输出切片保留尾部错误码**（`08 §2` 切片算法 6 步）；**`on_budget_exhausted=pause` → 任务进 `PAUSED` 且停止发包，`resume` 后从第 N+1 步续跑** |
| **B11** | 报告三视图 | MD/SARIF/JSON + ruleId + 双指纹 + verify-manifest + 措辞 lint + **弱绑定降级** | B4, B10 | `veripen/report/` `veripen_cli/cmd_report.py` | 三视图 finding 集合相同；VERIFIED 无 capsule → 渲染抛错；**弱绑定+high → 渲染抛错**；**三视图零地址**；手改报告 → manifest 校验失败；**摘要块输出 `reported/undetermined/error/not_run` 四态计数**；**`coverage_src=agent_reported` 的条目被降级为 `not_run`**；**§7 智能体行为审计五表齐全且越界次数=0 时显示绿色** |
| **B12** | bench | 44 例 × 3 轮 + 反作弊 + **routeset 一致性** | B9, B11 | `tests/bench/cases.yaml` `veripen_cli/cmd_bench.py` | `make data-verify` + `make range-selftest` 先绿；FP=0；越界=0；三轮 verdict 一致；**三轮 routeset_sha256 一致**；**渲染结果可由 routeset 推出**；`bench.md` 含未命中用例（分两类）与数据 sha256；**`error` 率 ≤2%（超了整轮作废）**；**`agent_reported` 覆盖记录数 = 0** |
| **B13** | 交付硬化 | doctor 16 项 + 离线包 + 3 worker + 死信 + canary | **B12.9（含 B12.1–B12.13 全部新节点）** | `veripen_cli/cmd_doctor.py` `scripts/package-offline.sh` `Makefile` | 断网 `docker load` 后 `doctor` 全绿（含 `data-verify`、`bindings-check`、路由卫生 5 条）；离线包含 `data/` 快照与 manifest；3 worker 并行不重不漏；死信单列；**`B12.1–B12.16` 全部 `done` 且 `make spec-drift` 0 差异**（十个表齐、17 自检红得掉、I1–I12 契约齐）|

### W3.6 · 补齐与扩容（v3.2 新增，**全部排在 B12 之后**）

> **排期原则（driver 定）**：B12 已开始推进，**不打断**。
> 四个补齐节点 + 多 agent 全部挂 **B12 之后**，作为 `B13` 的前置。
> 理由：B12 是"有没有数"的基线，基线没出来之前补的东西都无法验证；
> 而 B11.5/11.6 补完后，B12 的指标本身也要重跑一次（见 §5.1 重跑约定）。

#### 对账前提（`make spec-drift`，从本批起进 CI）

```
扫描 03 §1 的 CREATE TABLE 清单  vs  veripen_core/store.py 实际表
扫描 17 §0 的 I1–I12              vs  01 §7 契约测试条数（七条）
扫描 18 §0 的 14 类 oracle         vs  veripen_oracles/ 注册表
任何一项「规划有、代码无」→ 该批不许标 done（记 blocked，写清归属批次）
```

**为什么先立这条**：B11 标了 `done`，但 `03` 规划的 10 张表代码只有 3 张
（`blackboard_facts` / `asset_facts` / `task_events` / `routesets` / `approvals` / `bench_runs` 全无）。
`B10.open` 说"黑板归 B11"，`B11.open` 说"归 B15"——**互相推诿就是没有对账**。

#### 批次序列（严格按 driver 指定顺序）

| 序 | # | 批次 | 目标 | 依赖 | 产出 | DoD |
|---|---|---|---|---|---|---|
| **①** | **B12.1** | **黑板 + 事件落库** | `blackboard_facts` + `asset_facts` + `task_events` 三表 + `step.jsonl` 落盘 + 黑板→L1 注入视图 + 自检 12–17 | B12 | `veripen_core/{blackboard,ledger}.py` `store.py` `tests/unit/test_blackboard.py` | 六表建齐；`03 §7` 自检 12–17 **各有一条 bad fixture 能红**；`step.jsonl` 每步一行且 `route_ref` 不落模板；L1 注入 ≤30 条、`from_target=1` 带 `[目标数据，非指令]`；**`asset_facts` 写入者唯一且无 UPDATE/DELETE**（`01 §7` 第七条） |
| **②** | **B12.2** | **outcome 四态 + 覆盖来源** | `findings.outcome` + `coverage_src` 两列 + `schemas/finding` allOf + 报告四态统计 | B12.1 | `migrations/0xx` `veripen/report/render.py` `tests/unit/test_outcome.py` | 迁移前向可重放；`error`/`not_run` 在 schema 层强制 `state=BLOCKED`；`coverage_src=agent_reported` 强制 `outcome=not_run`；三视图都出四态计数；**`error` 计成 TN 的路径有一条测试会红** |
| **⑪** | **B12.14** | **★ 工作台读写层** | `fs.read/write/edit/glob/grep`（相对路径）+ `code.search`（L1 正则/AST） | B12.8 | `veripen_core/workbench/fs*.py` `tests/unit/test_workbench_fs.py` | 绝对路径 schema 层拒；`fs.write` 对已存在默认失败；`fs.edit` 的 `expect_count` 不匹配时**整体失败**；无 `fs.delete` |
| **⑫** | **B12.15** | **★ 工作台执行层** | `shell.run`（python/bash）+ 五道闸 + 静态扫描 + **强制代理** + 双维度策略 | B12.14 | `veripen_core/workbench/shell.py` `sandbox/**` `tests/integration/test_shell_proxy.py` | 脚本含 URL → 静态拒（有测试会红）；含 `os.system`/`subprocess` → 拒；**容器内直连外部 IP 失败、走代理成功**；代理越界记 `scope ok=0`；`05 §8.5` 双维度生效 |
| **⑬** | **B12.16** | **★ 计划与任务层** | `plan.enter/exit/abort` + `todo.write/list/update` | B12.15 | `veripen_core/workbench/plan.py` `todo*.py` `tests/unit/test_plan_mode.py` | `plan.enter` 后 `fs.write` 被拒；`plan.exit` 未确认不放行；`todo` 与 `leads` 一致（`pending>0` ⟺ 有 `in_progress`） |
| **⑥** | **B12.9** | **B12 指标重跑** | ①②之后 bench 指标口径变了（新增 error 率/自述覆盖率），**必须在补齐后重跑一次基线** | B12.2 | `bench.md` 重生成 | 三轮 `routeset_sha256` 一致；`error` 率 ≤2%；`agent_reported` 覆盖记录 = 0；**重跑前后 finding 集合差为空**（补齐不改结论） |
| **③** | **B12.3** | **workflow DAG 固化** | `18` 波次 → 显式 DAG（`workflow.yaml`：阶段/产物/门） | B12.1 | `config/workflow.yaml` `veripen_core/workflow.py` `tests/unit/test_workflow.py` | DAG 可加载且环检测生效；每阶段声明 `produces`（带 schema）与 `gate`（可失败可跳过）；阶段跳过必须落 `task_events`；**DAG 与 `18 §0` 波次列一致**（一致性测试） |
| **④** | **B12.4** | **knowledge（skill）层** | `knowledge/*.md` + 加载规则：**只进 L2，永不进判定** | B12.1 | `knowledge/**` `veripen_core/context.py` `tests/unit/test_knowledge_redline.py` | `test_knowledge_not_in_judge_path`（改 knowledge 全文后 verdict **逐字不变**）；knowledge 不进 `prompts/` 判定段；加载上限 ≤2 条/步、≤1KB/条 |
| **⑤** | **B12.5** | **★ 多 agent 编排** | `Recon` / `Verifier` / `Reporter` 三类执行体，**共享黑板与 routeset，分工不协商** | B12.1, B12.3 | `veripen_core/agents/**` `config/roles.yaml` `tests/unit/test_multi_agent.py` | 见 §3.5。核心：`test_agents_cannot_negotiate_verdict`；**单/多 agent findings 集合差为空** |
| **⑤b** | **B12.6** | **★ 自主渗透执行体 `act.*`** | 六类自由动作（`http_raw`/`http_fuzz`/`exec_tool`/`decode`/`compare`/`compose`）+ 容器沙箱 + 白名单二进制 + `lint-act-cmd` | B12.5 | `veripen_core/actions/**` `sandbox/**` `tests/unit/test_actions.py` | 六个动作各自跑通且**每个至少产一条 lead**；`exec_tool` 只接受 `target_ref`（无 host/url 字面量，`lint-act-cmd` 0 命中）；沙箱只读根 + 出网走 `egress_allowlist`；`test_actions_cannot_import_verdict` 红得掉 |
| **⑤c** | **B12.7** | **★ 线索闭环与强制验证** | `leads` 表 + `Lead` 类型 + `must_verify_next` + `pending_leads==0` 才 DONE + 报告「线索」章节 | B12.6 | `veripen_core/{leads,loop}.py` `veripen/report/render.py` `tests/unit/test_leads.py` | `test_pending_lead_forces_verify`；`verifiable_by=[]` 的 lead 直接 `expired` 不占预算；`pending>0` 时任务不能 DONE（标 `budget_exhausted=leads`）；**闭环率 bench ≥0.6**；报告里线索与 finding **分开列** |
| **⑤d** | **B12.8** | **★ 操作台 L0/L1 + 架构图落地** | `33`（八层图/九阶段/扩展矩阵）、`34`（TUI + 单页 + SSE 五事件）、`35` 动作卡片与线索面板 | B12.7 | `web/` `veripen_cli/cmd_tui.py` `api/stream.py` `tests/unit/test_stream.py` | 九部件齐全；SSE 五类事件都推得出；**`make lint-ui` 页面零地址**；`act.*` 卡片显示沙箱与退出码；**复制地址被阻止**；L0 TUI 在纯终端完成暂停/批准 |
| **⑦** | **B12.10** | **插件发现层** | 搜索语法 + `search_index.json` + 增量刷新（etag/pushed_at/`page_cursor`）+ 排序公式 | B12.8 | `veripen_core/plugins/discovery.py` `tests/unit/test_discovery.py` | 同快照 `score` **逐位可复现**（单测锁死）；6h 内重复刷新 **0 请求**；过期未超 7d → 先返旧结果标 `possibly_stale`；`--cached` 断网可用 |
| **⑧** | **B12.11** | **插件注册层** | `registry.json` + `lock.json` + `update-check`（6h 冷却）+ **复用发现层响应** | B12.10 | `veripen_core/plugins/registry.py` `tests/unit/test_registry.py` | **一次刷新上游请求 ≤2**（发现与注册共用响应）；`latest_known` 与 `installed_version` 严格分离；上游消失标 `deleted` 且**不自动卸载**；`rollback` 回到 lock 版本 |
| **⑨** | **B12.12** | **装配与准入** | 四类挂载点 + `policy.yaml` + 四适配器（npx/pip/mcp/raw）+ 网络代理 | B12.11 | `veripen_core/plugins/{assembly,adapters}/*` `policy.yaml` `tests/unit/test_plugin_policy.py` | `knowledge` 一键装且**不影响判定**；`oracle` 类**必须过 44 例 FP=0** 才准装；**插件直连目标被代理拦下并记事件**（红线 P1 有测试）；`npx` 传 `--no-install`、pip 加 `--only-binary` |
| **⑩** | **B12.13** | **插件 UI** | `34` 第四 Tab：搜索/缓存状态/版本箭头/挂载点色标/回滚 | B12.12 | `web/plugins/` `tests/unit/test_plugin_ui.py` | UI 必显缓存时间与下次检查；版本箭头 `a → b`；四挂载点色标；`lint-noroute` 对 `.veripen/plugins/**` **只放行 3 个 domain** 且 allowlist 带理由 |

**⑥ 为什么必须有**：①②改了数据库和报告口径，`B12` 那次 bench 的产物**不能直接当交付基线**。
不重跑，就会出现"报告说四态、bench 只有两态"的错位——那正是我们一直在防的"两处数字打架"。

### 3.5 多 agent 编排设计（B12.5，v3.2 新增）

> **前提：`99 P-10` 从"三期不做"改为"本期做"，但只允许一种形态——分工不协商。**
> 这个改动必须同时写进 `17 §0` 的削弱记录，因为它是唯一一条主动放宽"单 agent"的地方。

#### 3.5.1 三类角色，边界用类型焊死

| 角色 | 只做 | 不许做 | 产物类型 |
|---|---|---|---|
| **Recon** | 读黑板 + 读 routeset 摘要，产出"下一步该跑哪些 (oracle, route_ref)" | 不执行、不判定、不写 finding | `list[TaskSuggestion]` |
| **Verifier** | 执行 `run_oracle`，产出 `verdict` + `capsule` | 不改 plan、不写报告 | `Judgement`（不可变） |
| **Reporter** | 汇总 finding → 三视图 | 不改 verdict、不重算判定 | `Report` |

**关键设计**：`TaskSuggestion` 里**没有任何结论字段**（`verdict`/`severity`/`confidence` 全不在 schema 里）。
Recon 说"去测 X"，Verifier 说"结果是什么"，Reporter 说"怎么写"——**三段之间的接口上是空的**。

#### 3.5.2 与 I4 的冲突在哪，怎么解

| 冲突点 | 多 agent 的一般做法 | 我们只允许的做法 |
|---|---|---|
| 谁能出结论 | 多个 agent 各自判定、投票/协商 | **只有 `Verifier` 调用 `oracle.judge()`，其他人连函数都调不到**（`01 §7` 契约测试保证） |
| 冲突时怎么办 | agent 之间协商出一致答案 | **不协商**：两条不同的 `Judgement` 各自成 finding，报告里并列，由人判 |
| plan 冲突时 | manager agent 裁决 | `08 §1.2 NEXT_RULES` 条件表裁决（确定性、可单测） |

**一句话**：**agent 之间只传"任务"，不传"结论"。**

#### 3.5.3 并行度（这是多 agent 唯一真正的收益）

```
串行（单 agent）:  Recon → Verifier(1) → Verifier(2) → ... → Reporter
并行（多 agent）:  Recon → [Verifier(1) ‖ Verifier(2) ‖ Verifier(3)] → Reporter
```

**并行边界**（哪些能真并行）：

| 可以并行 | 不可以并行 |
|---|---|
| 不同 oracle 对不同资产 | 同一 `(task, oracle, asset)` 的多次执行 |
| 同一资产的不同 `route_ref`（若 `per_asset_concurrency` 允许） | 写操作类 oracle（`mass_assignment` / `xss_stored_probe`，会互相污染状态） |
| OOB 监听与验证 | 审批门未过的动作 |

**并发上限**：`per_asset_concurrency`（`02 §policy`，默认 2）仍然是硬约束——
**多 agent 不能用来绕限流**，这条写进 `guard.ratelimit` 的测试。

#### 3.5.4 验收（B12.5 的硬 DoD）

| # | 断言 | 命令 | 为什么必须有 |
|---|---|---|---|
| 1 | **交互产物无结论字段** | `test_agents_cannot_negotiate_verdict` | 文字纪律守不住，必须 schema 级 |
| 2 | **单/多 agent findings 集合相同** | `make bench-abi` 比对 `bench.single.json` 与 `bench.multi.json` 的 finding 集合差为空 | 多 agent 不许改变结论 |
| 3 | **多 agent 更快但不多发请求** | `requests_multi == requests_single` | 并行不等于加压 |
| 4 | **崩溃隔离** | 杀一个 Verifier，任务仍完成，该 oracle 标 `outcome=error` | 多 agent 增加了故障面，必须证明不传染 |
| 5 | **成本不失控** | `tokens_multi ≤ 1.3 × tokens_single` | 多 agent 最贵的失败是"每个都重读一遍上下文" |

第 2 条是**多 agent 的生死线**：如果加了 agent 结论就变了，那它就不是"加速"，是"引入不确定性"。

#### 执行顺序表（交给下一位 agent 直接照跑）

> **一句话**：B12 已推进中，**先把 B12.1–B12.13 全部做完，才允许开 B13**。
> 每批仍按 `§2.5` 的"只读那几节 + 跑 DoD 命令"，不要通读全册。

| 序 | 批次 | 一句话目标 | 前置 | 退出信号 |
|---:|---|---|---|---|
| 0 | **B12** | bench 基线（进行中） | B9, B11 | FP=0 且三轮一致 |
| 1 | **B12.1** | 黑板 + 事件落库（**最先做，后面全依赖它**） | B12 | 六表齐 + 自检 12–17 红得掉 |
| 2 | **B12.2** | `outcome` 四态 + 覆盖来源 | B12.1 | 报告出四态计数；`error` 计成 TN 的路径有测试会红 |
| 3 | **B12.9** | **指标重跑**（口径变了，旧 bench 不能当基线） | B12.2 | 重跑前后 finding 集合差为空 |
| 4 | **B12.3** | workflow DAG 固化 | B12.1 | DAG 与 `18 §0` 波次一致 |
| 5 | **B12.4** | knowledge（skill）层，只进 L2 | B12.1 | 改 knowledge 全文 verdict 逐字不变 |
| 6 | **B12.5** | 多 agent（分工不协商） | B12.1, B12.3 | 单/多 agent finding 集合差为空 |
| 7 | **B12.10** | 插件发现层 | B12.8 ⚠ | score 可复现；6h 内刷新 0 请求 |
| 8 | **B12.11** | 插件注册层 | B12.10 | 一次刷新请求 ≤2 |
| 9 | **B12.12** | 装配与准入 | B12.11 | oracle 必过 44 例；插件直连被拦 |
| 10 | **B12.13** | 插件 UI | B12.12, B12.8 ⚠ | 四挂载点色标；缓存时间可见 |
| 11 | **B12.14** | 工作台读写层 `fs.*` | B12.8 | 绝对路径拒；无 `fs.delete`；`fs.edit` 不匹配即整体失败 |
| 12 | **B12.15** | 工作台执行层 `shell.run` + **代理** | B12.14 | **容器直连失败、走代理成功**；脚本含 URL 即拒 |
| 13 | **B12.16** | `plan` / `todo` | B12.15 | `plan.enter` 后写被拒；`todo` 与 `leads` 一致 |
| 11 | **B12.14** | 工作台读写层 `fs.*` | B12.8 | 绝对路径拒；无 `fs.delete` |
| 12 | **B12.15** | 工作台执行层 `shell.run` + **代理** | B12.14 | **容器直连失败、走代理成功**；脚本含 URL 即拒 |
| 13 | **B12.16** | `plan` / `todo` | B12.15 | `plan.enter` 后写被拒 |
| — | **B13** | 交付硬化 | **全部 B12.x** | `spec-drift` 0 差异 + doctor 全绿 |

> ⚠ **顺序依赖提醒**：`B12.10` 与 `B12.13` 依赖 **B12.8（操作台）**，而 B12.8 排在 B12.5 之后。
> 实际执行顺序应为：`B12.1 → B12.2 → B12.9 → B12.3 → B12.4 → B12.5 → B12.6 → B12.7 → B12.8 → B12.10 → B12.11 → B12.12 → B12.13 → B12.14 → B12.15 → B12.16`。
> 上表第 7–10 行是"内容顺序"，不是"时序顺序"——时序看这条。

#### 交接清单（给下一位 agent）

开工前必须先做的三件事：

1. `git checkout -b feat/b12-completion`（不要直接改主分支）
2. 跑 `make spec-drift`，把当前"规划有、代码无"清单**存一份到 `out/b12-completion/baseline.txt`**
   —— 这是后面每一批的对照基线，也是 `B13` 的 DoD 证据
3. 读 `21 §2.5` 的 B12.x 行 + 对应规格（`03 §1/§7`、`09 §7`、`33`、`35`、`36`），**不要通读 37 篇**

每题开工只读三处：
| 批次 | 必读 |
|---|---|
| B12.1 | `03 §1`（10 表）、`03 §7`（17 自检）、`08 §4`（L1/L1a） |
| B12.2 | `10 §3.0`、`09 §3`、`schemas/finding.schema.json` |
| B12.9 | `10 §3`（19 指标） |
| B12.3 | `18 §0`（波次列）、`33 §2`（九阶段） |
| B12.4 | `36 §4.1`（knowledge 准入）、`08 §4` |
| B12.5 | `21 §3.5`（**只读这一节**）、`17 I12` |
| B12.6 | `35 §4`（六动作表）、`35 §5`（地址不入库） |
| B12.7 | `35 §2`（Lead）、`35 §3`（强制闭环） |
| B12.8 | `33`、`34` 全篇 |
| B12.10–13 | `36` 全篇 |
| B12.14 | `38 §3`、`38 §9` |
| B12.15 | `38 §4`（**五道闸 + 代理**）、`05 §8.5` |
| B12.16 | `38 §5` |

### W4 · 平台化

| # | 批次 | 目标 | 依赖 | 产出 | DoD |
|---|---|---|---|---|---|
| **B14** | TierC 3 类 | xss_stored_probe / cors_misconfig / graphql_introspection | B7 | `veripen_oracles/{3 个}/` | 跨会话存储型可判；CORS 预检+GET 双测；GraphQL 单绑定多 operation 表达正确 |
| **B15** | Web 控制台（只读） | 任务/发现/capsule/绑定浏览 + replay 按钮 | B11 | `web/`（FastAPI + 单页静态） | 只读：`ui.*` 写操作 405；replay 按钮调 CLI 等价 op；越权访问 403；**绑定页只显示摘要，无模板正文** |
| **B16** | 第二客户档 | `profiles/demo-b`（车云形态）+ demo-alt | B13 | `profiles/demo-b/` | `make demo-alt` 跑通；`git diff --stat veripen_core/` 为空；`lint-noleak` 通过；**`lint-noroute` 通过**；两个档的 selectors 差异只在 YAML |
| **B17** | 交付物料 | 演示脚本 + 录屏 + 一页速查 + playbook + 分发清单 | B16 | `docs/delivery/` `scripts/demo.sh` | 5 分钟演示一次过（含"删绑定重发现"这一段）；断网兜底可跑；清单 10 项全勾 |
---
## 2.6 交付收尾批次 B18–B21（**v3.6 新增**，接在 B17 之后）

> **起因**：v3.5 全 33 批 `done` 后复核发现——`16 §4` 十二条断言里 **7 条没有 `make` 命令**，
> 且 `ModelDecider.client` / `Executor` / `act.exec_tool` 的 `run` / 插件 fetch 四处注入点全空。
> 详见 `39-交付收尾与能力接驳规格.md`。
>
> **排期原则**：不打断任何已 `done` 批次；四批全是**接线与验证面扩容**，不改判定逻辑、不改内核契约。

### 2.6.1 批次状态枚举（v3.6 扩充）

> **进度：B18–B29 全部 `done`（2026-11-03）。B29 为验证批次，结论是负的（见行内）。`B20-sandbox` `blocked`（无 docker/WSL）。**
> 39 §4.2 的四批已收口，B22 补其最后一处口径缺口；A10/A11/A12 三条断言全部转绿，且各有反例证明能红。
> **B22 不是新能力，是把 B21 的闭环从「同语义」收紧到「同目标」**：证据见 `tests/integration/test_bench_leads_shared_target.py`。

| 状态 | 含义 | 计入完成分母 | 用在哪 |
|---|---|---|---|
| `done` | DoD 全绿 **且** I14 能力接驳门通过 | ✅ | 全部 |
| **`scaffolded`** | **形状齐（协议/类型/签名/闸门/测试）但注入点空** | **❌** | v3.5 中实际处于此态的批次 |
| `blocked` | 缺前置，写清归属 | ❌ | 按 `39 §6` |
| `done(legacy-scope)` | 历史口径批次，按当时 DoD 结 | ✅（但需按 `39 §2.3` 复核注入点） | B13–B17 |

> **B1–B12.16 现状复核结论**：其中 **B10（agent 循环）、B12.6（`act.*`）、B12.10–B12.13（插件）、
> B12.14–B12.16（工作台）** 按 I14 口径**应改标 `scaffolded`**——它们的 DoD 全部只验了形状。
> **本批不直接改历史记录**（改历史等于造假），而是在账本新增 `i14_review` 字段留痕，
> 由 B18–B21 逐批把状态提升回 `done`。

### 2.6.2 四批批次卡

| 序 | # | 批次 | 目标 | 依赖 | 产出（允许改的路径） | DoD（**每条都必须能红**） |
|---:|---|---|---|---|---|---|
| ① | **B18** ✅ `done` | **断言命令落地 + 模型接驳** | 补 A2/A4/A6/A7/A8 命令；A10/A11/A12 命令先落地（允许红）；接 `Decider.client` | B17 | `Makefile` `veripen_cli/cmd_{replay,cases,coverage,bench_leads,plugin_roundtrip,workbench_smoke}.py` `veripen_cli/_io.py` `scripts/lint/noleak.py` `veripen_core/agent/client.py` `veripen_core/agent/loop.py`（build_decider） `tests/unit/test_model_client.py` | ✅ **已达成**：7 命令全在且各有一条反例能红（A2 抓到真实客户串、A4 篡改→TAMPERED、A6 删用例→未覆盖、A7 打乱→摘要变）；`--model none` 与默认逐字同结果；`build_decider` 无 client 时等价 scripted；窄 schema 拒多调用/未知工具；坏输出 3 次降级；`veripen_cli/` 首次进 mypy strict（91 文件 0 error） |
| ② | **B19** ✅ `done` | **执行接驳** | `Executor` 真实现（subprocess + 超时 + `08 §2` 切片）+ `act.exec_tool` 真跑 | B18 | `veripen_core/workbench/executor.py` `veripen_core/workbench/shell.py`（elapsed 实测） `veripen_cli/cmd_bench_leads.py` `tests/integration/test_executor.py` | ✅ **已达成**：`shell.run` 真得 stdout、超时 exit=124 且 elapsed≥500ms、env 只透传白名单；`act.exec_tool` 真跑 `ffuf` 打本地服务得 2 命中产 lead；非白名单仍拒；输出走 `08 §2` 六步切片（has notice + persisted_ref）；A12 转绿、A10 到 4/6 |
| ③ | **B20** ✅ `done` | **插件网络 + http 传输** | `sandbox/**` 真隔离（容器 / Windows Job+landlock）+ 插件 fetch | B19 | `veripen_core/plugins/fetch.py` `veripen_core/actions/sender.py` `veripen_cli/cmd_bench_leads.py` `veripen_cli/cmd_plugin_roundtrip.py` `tests/integration/test_plugins_http.py` | ✅ **部分达成**：fetch 真验 etag/304/预算且 Result 无地址（A11 转绿）；http_raw/http_fuzz 真发包（A10 到 6/6）；executor 加进程组回收。**容器隔离未做**（无 docker/WSL），已拆为 B20-sandbox `blocked` |
| ⑤ | **B20-sandbox** ⛔ `blocked` | **容器真隔离** | 无 docker / 无 WSL（本机实测），`SandboxNetwork` 仍是建模层 | — | `sandbox/**` | 起了容器并能证明「容器内直连外部 IP 失败、走代理成功」才可开 |
| ④ | **B21** ✅ `done` | **落库闭环** | `asset_facts` 写入点 + `todo` 持久化 | B20 | `veripen_core/blackboard.py`（AssetFacts） `veripen_core/workbench/plan.py`（TodoStore） `veripen_core/store.py`（todos 表） `veripen_cli/cmd_bench_leads.py` `scripts/b12/run_bench.py` `tests/unit/test_closure_persistence.py` | ✅ **已达成**：`asset_facts` 四道门（无 capsule/未 seal/无 evidence/target 未标记）全拒且可写可读；`todo` 跨 Store 实例持久化；闭环率 **bench 用真 pair 数（150）+ leads 表真统计**，`test_closure_rate_moves_with_state` 证明它不是恒等式（0.0→0.5→0.333）；`bench-leads` 闭环率 **1.00（6/6）**，每条 lead 真跑对应 oracle |
| ⑥ | **B22** ✅ `done` | **同目标闭环** | act.* 与 verify.* 共用**同一个** lab 实例（同 App / 同端口 / 同一份 log），把「回环通了」收紧成「同目标闭环」 | B21 | `veripen_cli/cmd_bench_leads.py` `tests/lab/net_server.py` `tests/integration/test_bench_leads_shared_target.py` | ✅ **已达成**：`target.act_requests/verify_requests` 从**同一 `app.log`** 切出（12 + 42），`test_act_and_verify_share_one_app_instance` 在换实例时必红（新实例 `act_requests` 归零）；闭环率仍 **1.00（6/6）**；客户端半路断连不再打 traceback 污染验收输出。**声明缺口未修**：lab `assets_json` 把 search 端点声明成无占位符模板（`param_names` 里却有 `q`），act 按实现写模板，改声明会波及 44 例 bench 基线，登记为 open |
| ⑦ | **B23** ✅ `done` | **契约显式化** | lab 声明的带参端点补显式占位符（`?q={q}` 等），让 `act.*` 的模板能从声明派生；顺手统一三条渲染路径 | B22 | `tests/lab/app.py` `tests/lab/net_transport.py` `veripen_cli/cmd_bench_leads.py` `tests/integration/test_lab_network.py` | ✅ **已达成**：`http_raw`/`http_fuzz` 的模板与参数名改从 `assets_json()` 取；`findings_diff` **44 条前后一致**（TP26/FP0/FN2 不变）；新增 2 条测试在旧 `render_request` 下必红（实测 `q={q}&q=widget` → 现 `q=widget`）。**踩到并修掉一个真 bug**：真网络传输只替换 path 段的占位符，声明一旦带 query 占位符就会双写并让服务端读到字面量 `{q}`，正例集体退化成 REFUTED |
| ⑧ | **B24** ✅ `done` | **绑定形态对齐** | 动作层 sender 接受**绝对模板**（host 来自模板），与路由层 `_host_registered` 的既有约定对齐；act.* 改吃 `_routeset` 的绑定，删掉手写 `_binding_for` | B23 | `veripen_core/actions/sender.py` `veripen_cli/cmd_bench_leads.py` `tests/integration/test_plugins_http.py` | ✅ **已达成**：绝对模板走模板自带 host、相对模板仍走 `resolve_base`；`resolve_base` 故意给死地址时绝对模板请求仍 200（证明 host 不来自 base）；新增 2 条测试在旧实现下必红。act 与 verify 现在用**同一批** `RouteBinding`，`_binding_for` 与两个派生 helper 全部删除。`findings_diff` 44 条一致、`bench-leads` 6/6 |
| ⑨ | **B25** ✅ `done` | **传输规则同构** | Wsgi 版模板渲染改成与真网络版同一条规则（占位符在**整条模板**上替换，固定 query 保留） | B24 | `tests/lab/transport.py` `tests/integration/test_lab_network.py` | ✅ **已达成**：两套传输对同一条模板给出同一个 query；带固定 query 的构造模板下旧行为必红（实测 `mode=all&q=x` → 旧版只剩 `q=x`）；`findings_diff` 44 条一致、`pytest` 1114 passed |
| ⑩ | **B26** ✅ `done` | **真模型接驳 + 主要功能测试** | 接真实 LLM（`client.from_env()`）跑 agent 驱动的主要功能测试；修四处真缺陷 | B25 | `veripen_core/agent/client.py` `veripen_core/agent/loop.py` `veripen_core/tools/registry.py` `tests/unit/test_agent_loop.py` `scripts/b26/agent_live.py` | ✅ **已达成**：真模型 16 步 → 10 个 oracle 全 VERIFIED，与纯 scripted **共有判定 10 条、结论冲突 0 条**。修掉四处：① `Python-urllib` UA 被网关 WAF 403；② `json_object` 要求 prompt 含小写 `json`；③ `ALLOWED_TOOLS` 取 `OUTER_FIELDS` 而非 `SCHEMAS`，模型被承诺了执行不了的 `list_assets`；④ 模型合法打转时降级链不动、循环直接收敛（新增 idle→escalate） |
| ⑪ | **B26b** ✅ `done` | **模型真正主导** | prompt 加 few-shot + 动作指令；修 `rediscover_routes` 恒记 facts+1 | B26 | `veripen_core/agent/client.py` `veripen_core/agent/loop.py` | ✅ **已达成**：模型**不经降级**跑完 11 步——10 次 `run_oracle` 全 VERIFIED、末尾自己 `finish`，决策 `converged(finished)`；与 scripted 冲突 **0 条**。B26 时模型前 3 步只会列清单、第 4 步就降级，本批之后不再降级 |
| ⑫ | **B27** ✅ `done` | **模型成本量化** | 客户端记录网关回报的 usage；量一次完整审计的调用数与 token | B26b | `veripen_core/agent/client.py` `scripts/b26/agent_live.py` | ✅ **已达成**：11 次调用 / prompt 7485 + completion 753 tokens / 70.8s，10 个 oracle 全 VERIFIED、冲突 0。**同时测出一条负结论**：在 lab 固定 pending 下，模型挑的 oracle 与 scripted 完全相同——多花 8.2k tokens 换 0 增量，模型的价值必须到「pending 不由 case 预先给定」的真实资产上才成立 |
| ⑬ | **B28** 🟡 `scaffolded` | **真实资产通道** | `veripen hunt`：契约 → RouteSet → 守卫链 → 真发包 → 判定，指向任意 TARGET | B27 | `veripen_cli/cmd_hunt.py` `veripen_oracles/live.py` `veripen_contract/planner.py` | **形状齐**：lab 当 TARGET 实测 8 条绑定、10 个 oracle、32 次真请求，每次出网过 `binding_gate`→`preflight`→`post_connect`。**修掉一处真 bug**：`plan()` 的归一占位符 `{p}` 与 `SymbolSource._render` 不兼容，「契约 → RouteSet」一直得到 0 条绑定。**空缺**：token/oob/symbols 三个注入点为空 → 受保护面验不出来，0 确认是「没带身份」 |
| ⑭ | **B28b** ✅ `done` | **凭据注入点** | `--token` / `--token-secondary` / `--oob` / `--symbols`；未消费参数拼进 query | B28 | `veripen_cli/cmd_hunt.py` `veripen_oracles/live.py` | ✅ **已达成**：同一目标、同一份契约，带凭据后确认数 **0 → 6**（idor / open_redirect / path_traversal / sqli×2 / xss）；空 token 不发 `Authorization` 头（发了会被目标记成无效凭据，掩盖「根本没带身份」） |
| ⑮ | **B29** ✅ `done` | **模型发现能力** | `--discover` 探索模式（拿掉「pending 非空就 run_oracle」这句指令）+ `--no-fallback` 纯模型对照 | B28b | `veripen_core/agent/client.py` `scripts/b26/agent_live.py` | **负结论**：纯模型 3 步全探路（list_assets / list_routes）后 idle 收敛，`started_verifying=False`、`coverage=0.0`——**不会自己进入验证**。带降级时 `coverage=1.0` 但模型只跑了 3 步，验证全部由 scripted 完成。**B27 的负结论由此升级为普遍结论**：模型在窄 schema + 探索指令下只做只读探索，「能打」靠 scripted + oracle 库 |

### 2.6.3 注入点全表（`39 §2.3` 的落地版，谁负责接）

| 注入点 | 定义位置 | 当前实现 | 归属批次 |
|---|---|---|---|
| `Decider.client` | `veripen_core/agent/loop.py` | 无 | **B18** |
| `workbench.shell.Executor` | `veripen_core/workbench/shell.py` | 无（仅测试传入） | **B19** |
| `actions.exec_tool` 的 `run` | `veripen_core/actions/__init__.py` | 无（只产 argv） | **B19** |
| `SandboxNetwork` 的真隔离 | `veripen_core/workbench/shell.py` | 无（建模） | **B20** |
| `plugins` 的 fetch | `veripen_core/plugins/discovery.py` | 无（只有预算计数） | **B20** |
| `asset_facts` 写入点 | `veripen_core/blackboard.py` | 无 | **B21** |
| `todo` 持久化 | `veripen_core/workbench/plan.py` | 无（内存） | **B21** |

### 2.6.3b 后续批次（B29–B34）见 `40-验证方案与批次规划.md`

> `39 §4.2` 的四批 + B22–B28b 已全部收口。后续**验证驱动**的批次不再在本篇逐条展开，
> 统一落在 `40` 篇：B29（模型发现能力）/ B30（真实资产召回）/ B31（弱模型）/
> B32（成本）/ B33（三档汇总）/ B34（凭据管理），每批都带能红的判据与 D1–D4 红线。
>
> **为什么不并回本篇**：本篇记的是"实现批次"，`40` 记的是"验证批次"。
> 两者的完成定义不同——前者看注入点有没有接上，后者看判据达没达标。
> 混在一起会让"接上了"冒充"验过了"。

### 2.6.4 P2 决策项（**需 driver 拍板**）

| 选项 | 含义 | 代价 |
|---|---|---|
| **A. 守住 D-05**（只打本地靶场） | 对外只讲「验证内核」；A 档永不可达 | 承诺兑现、证据可信 |
| **B. 放开 D-05** | 接 1 个非自建目标（DVWA / Juice Shop 容器） | 重钉 bench 基线、扩分母、重跑三轮；「只打本地」卖点改写；`16 §7` Parking Lot 同步 |

> **未拍板前口径不变**：`16 §3` C5 范围与 `10` 的 44 例分母继续按 D-05 执行。

## 2.5 批次附加信息（读哪节 / 行数 / 回滚 / 预案）

> 每批开工前只看这张表的对应行，不用翻全册。行数是**含测试**的估计，用于判断"这批是不是太大了"。

| # | 必读（只这几节） | 预计行数 | 回滚方式 | 失败预案 |
|---|---|---|---|---|
| B1 | `01 §1–§3`、`03 §1`、`24 §3` | 600 | 删 `veripen_core/` + `api/`，重跑 compose | 镜像拉不动 → 先做 `package-offline` 的拉取部分 |
| **B1.5** | **`27` 全篇、`01 §3.1/§3.3`** | **700** | **删 `veripen_core/routes/` + `tests/routes/`** | **确定性不过 → 先砍并发，只留单线程解析；再查排序键** |
| B2 | `04 §1–§4`、`03 §2` | 350 | `git revert` 单批提交 | 时钟漂移 → 统一用 Redis `TIME` |
| B3 | `05 §1–§3`、`24 §4(2xxx)` | 450 | 同上 | 令牌桶不准 → 用 Lua 原子实现，别用本地内存 |
| B4 | `06 §2–§5` | 500 | 同上 | 哈希不稳 → 规范化 JSON；注意 `path_rendered` 不进哈希 |
| B5 | `18 §1`、`07 §0.2–§0.4`、`25 §3.3` | 900 | 删 4 个 oracle 目录 | OOB 不通 → 先跑 `make range-selftest` |
| B6 | `18 §1.5–§1.6` | 400 | 删 2 个 oracle 目录 | 指纹库命中率低 → 扩 `fingerprints.yaml` |
| B7 | `18 §2`、`05 §8`（写操作边界） | 800 | 删 5 个 oracle 目录 | 写类 oracle 误伤 → 默认 `enabled:false`，只在授权档开 |
| B8 | `19` 全篇、`24 §5(cases)` | 750 | 删 `veripen_contract/` | 生成不稳定 → 强制按 `template_sha256` 排序 |
| B9 | `20 §2–§5`、`18 §1.4` | 500 | 删 `checks/` + jwt 扩展 | mTLS 环境缺证书 → 该条标 BLOCKED 不算失败 |
| B10 | `08` 全篇、`04 §7` | 650 | 删 `agent/` `tools/` | 模型不稳 → 降级 scripted planner，先保流程 |
| B11 | `09` 全篇、`24 §4(6xxx)` | 700 | 删 `veripen/report/` | SARIF 不过 → 用官方 schema 离线校验 |
| B12 | `10` 全篇、`11 §4` | 550 | 删 `tests/bench/` | 用例不稳 → 先跑 `range-selftest`，再跑 bench |
| B13 | `12 §2–§4`、`23 §5`、`24 §5` | 600 | 删 `scripts/package-offline.sh` + doctor | 离线包过大 → 拆模型为可选包 |
| B14 | `18 §3` | 450 | 删 3 个 oracle 目录 | 存储型不稳 → 用 HTTP 直读，不引无头浏览器 |
| B15 | `22 §2`、`24 §2.6` | 500 | 删 `web/` | 页面太慢 → 只渲染服务端表格，不做前端框架 |
| B16 | `02 §4–§6`、`14 §4` | 250 | 删 `profiles/demo-b/` | selectors 写不对 → 用 `make routes ASSET=y` 逐条核对标签 |
| B17 | `22 §7`、`12 §9`、`24 §5`、**`32 §5`** | 200 | 删 `docs/delivery/` | 演示超时 → 用 `demo-offline` 兜底 |

**判断"这批太大"的信号**：预计行数 > 900 或 DoD 里出现 3 条以上互不相关的断言 → 拆成 a/b 两批，
但**必须保持同一批次号加后缀**（B5a/B5b），且拆批要记进 `batches.jsonl` 的 `notes`。

## 3. 依赖图（谁可以并行）

```
B1 ──┬── B1.5 ──┬── B3 ──┐
     ├── B2 ─────┤        │
     └───────────┴── B4 ──┴── B5 ──┬── B6 ── B8 ──┐
                                   ├── B7 ──┬── B9 ──┤
                                   └────────┴── B10 ──┼── B11 ── B12 ── B13 ── B16 ── B17
                                                       └── B14 ──┘         │
                                                             B15 ◀─────────┘
```

**可并行的三条 lane**（互不碰同一批文件）：

| lane | 批次 | 独占路径 |
|---|---|---|
| L-CORE | B2, B3, B4, B10 | `veripen_core/`（除 guard 与 routes 按批次划） |
| L-ORACLE | B5, B6, B7, B14 | `veripen_oracles/` |
| L-SURFACE | B8, B9, B11, B15 | `veripen_contract/` `checks/` `veripen/report/` `web/` |

**合并点**：B12（bench）与 B13（交付）必须单线程做，因为它们读全部产出。
**冲突规则**：lane 之间只允许通过 `veripen_core/oracle_api.py`、`models.py`、`routes/types.py` 交互；
改这三个文件要单独提一个批次（不在并行期内改）。

**B1.5 不可并行**：它是所有 lane 的类型基础。它没冻结，别的 lane 都在猜接口。

## 4. Agent 执行协议（每批照做，不许跳）

```
1  读：17 §0 十二条不变量（I1–I12） + 17 §3 四态账本 + 本批次卡 + 卡里点名的规格文档（不要通读 37 篇）
2  改：只允许动本批次的"产出路径"；越界即停
3  验：跑 DoD 命令，把**完整输出**贴进批次记录（不许写"应该可以"）
4  阻：真实失败允许开一条临时支线（绑定父批次、记触发证据、一层、关闭后回归）
       —— 支线不得改产品范围、不得新增完成分母
5  记：更新 .veripen/batches.jsonl 一行（见 §5）
6  停：本批次 DoD 全绿即停，不顺手做下一批
```

**禁止事项**

| 禁止 | 原因 |
|---|---|
| 一次跑多批 | 失败无法定位，回滚范围过大 |
| 顺手"优化"已验收代码 | 破坏既有证据 |
| 为了让 DoD 过而放宽断言 | 等于没测 |
| 新增未登记的依赖 | 离线包会炸 |
| 在并行期改 `models.py` / `oracle_api.py` / `routes/types.py` | 三条 lane 同时炸 |
| **在 `config/noroute.allowlist` 里加白来让 lint 通过** | 那是把违规合法化；先修代码 |
| **在 oracle 里"临时"写一个默认路径** | 这就是 hardroute 的诞生方式；写不出就走 `BLOCKED` |

## 5. 批次账本（唯一进度事实源）

`.veripen/batches.jsonl`，每批一行：

```json
{"batch":"B1.5","state":"done","started":"2026-11-02T09:00Z","ended":"2026-11-02T12:40Z",
 "agent":"claude-x","files":["veripen_core/routes/*","tests/routes/*"],
 "dod":[{"cmd":"pytest tests/routes","exit":0,"passed":38,"failed":0},
        {"cmd":"pytest -k routes_contract","exit":0,"passed":3,"failed":0}],
 "evidence":["out/b1.5/routeset_deterministic.log"],
 "open":[], "notes":"排序键改为 template_sha256 后确定性稳定"}
```

| 字段 | 用途 |
|---|---|
| `state` | `todo / doing / done / blocked` |
| `dod[]` | 命令 + 退出码 + 计数，机器可读 |
| `evidence[]` | capsule / 日志路径，可点开 |
| `open[]` | 遗留问题（进 99 或下一批） |

**进度口径**：完成度 = `done` 批次 / 18，**且**每批 DoD 有证据。没有证据的 `done` 不算。

## 6. 工期估算（agent 辅助，含验证与集成）

| 波 | 批次 | 单批 agent 轮次 | 日历（串行） | 日历（3 lane 并行） |
|---|---|---|---|---|
| W1 | 5（含 B1.5） | 2–4 | 3.5 天 | 2.5 天 |
| W2 | 5 | 2–5 | 4 天 | 2.5 天 |
| W3 | 4 | 2–4 | 3 天 | 2 天 |
| W4 | 4 | 2–4 | 3 天 | 2 天 |
| **合计** | **18** | — | **13.5 天** | **9 天** |

**说明**：这里的天数是"人在环里"的天数——每批结束你要看 DoD 输出并拍板，不是无人值守。
纯 agent 无人值守跑 30 批的失败率接近 100%，因为没有中间证据。

**B1.5 的额外成本说明**：它比原计划多 0.5 天，但它省下的是"B5–B7 全部重写"。
v2 的教训就在这里：先写 oracle 再补路由，等于把 14 个 oracle 全部返工。

## 7. 为什么这个粒度是对的

| 判断 | 依据 |
|---|---|
| 一批 ≤ 1 个可执行 DoD | 一批多目标 → 失败无法归因 |
| 一批 ≤ 400 行改动 | 超过就难 review，回滚成本高 |
| 每批都有可跑命令 | 拒绝"看起来完成了" |
| 每批都能独立回滚 | 依赖图保证只有下游受影响 |
| 14 个 oracle 不削 | 每个都是独立插件，加一个不碰内核，成本是线性的 |
| **路由层单独一批** | 它是类型基础，混在别的批次里会被"顺手"改坏 |

## 8. 进度看板（贴墙版）

```
W1 ▢B1 ▢B1.5 ▢B2 ▢B3 ▢B4        → 冷启动门 + A9
W2 ▢B5 ▢B6 ▢B7 ▢B8 ▢B9          → A6 覆盖 + A7
W3 ▢B10 ▢B11 ▢B12 ▢B13          → A3/A4/A5/A8
W3.6 ▢B12.1 ▢B12.2 ▢B12.3 ▢B12.4 ▢B12.5 ▢B12.9 → 对账清零 + 多 agent
W3.7 ▢B12.6 ▢B12.7 ▢B12.8            → **能打同时能证明**（A10）
W3.8 ▢B12.10 ▢B12.11 ▢B12.12 ▢B12.13 → **A11 插件可扩展**
W3.9 ▢B12.14 ▢B12.15 ▢B12.16          → **A12 能读写能执行**（长出两只手）
W4 ▢B14 ▢B15 ▢B16 ▢B17          → A1/A2/A6/A7
```

每完成一批，把 ▢ 改 ▣，并在 `.veripen/batches.jsonl` 追加一行。**账本是唯一事实，看板只是它的投影。**

## 9. 与 17 的关系

`17` 定"权威与四态"，本文定"怎么一批批吃掉全量"。
开工前需要 `17 §6` 的 driver 确认；确认后从 `B1` 开始，一次只跑一批。
