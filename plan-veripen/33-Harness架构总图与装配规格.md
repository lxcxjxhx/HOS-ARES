# 33 · Harness 架构总图与装配规格（v3.3 新增）

> 本册前面 32 篇是**零件规格**，这篇是**装配图**。
> 回答三个之前没人回答的问题：
> ① 一次任务从 `POST /tasks` 到 `DONE` 到底经过几层、几个阶段？
> ② 渗透场景**特有**的组件是哪几个（通用 harness 没有的）？
> ③ 加一个新东西（oracle / playbook / 工具 / 行动作）要动哪几处？

---

## 0. 一句话

> **一个循环、两类动作、一条证据链。**
> 探索（`act.*`，自由）与验证（`verify.*`，纯函数）**跑在同一个会话、同一份黑板、同一套预算里**，
> 探索产出的每条线索（lead）**必须在同一次运行内被验证或明确标记为待验证**——
> 不允许"打完了但说不清"，也不允许"证明了但不是自己打的"。

---

## 1. 八层运行时（谁在哪一层）

```
┌─────────────────────────────────────────────────────────────────────┐
│ L8 接口层     CLI / HTTP API / 控制台(34) / Worker 领取             │
├─────────────────────────────────────────────────────────────────────┤
│ L7 编排层     Workflow DAG（B12.3）→ 阶段推进；单 agent 循环（§2）   │
├─────────────────────────────────────────────────────────────────────┤
│ L6 决策层     Planner：选下一动作（act.* 或 verify.*）＋ 依据        │
│               ├ 规则表 NEXT_RULES（08 §1.2，确定性兜底）             │
│               └ 模型（可换，A8/A8+ 保证判定不变）                    │
├─────────────────────────────────────────────────────────────────────┤
│ L5 上下文层   黑板(事实) + L1a(资产事实) + L0(路由摘要) + L2/L3      │
│               + knowledge（按需注入，B12.4）                        │
├─────────────────────────────────────────────────────────────────────┤
│ L4 动作层     ┌ act.*  自由动作：shell/工具/payload 构造（35）      │
│               └ verify.* 验证动作：oracle 参数化调用（07/18）        │
├─────────────────────────────────────────────────────────────────────┤
│ L3 门控层     scope 四处 + 绑定门 + 预算 + 审批门 + 令牌桶（05/04）  │
├─────────────────────────────────────────────────────────────────────┤
│ L2 判定层     oracle.judge() 纯函数（I1/I2）——**只有这里能出 verdict**│
├─────────────────────────────────────────────────────────────────────┤
│ L1 证据层     capsule / leads / 哈希链 / 六态 replay（06/35）        │
└─────────────────────────────────────────────────────────────────────┘
```

**贯穿全层的四条铁律**（来自 `17 §0`，此处只列与本图强相关的）：

| 铁律 | 落在哪一层 | 机械保证 |
|---|---|---|
| I4 模型不参与判定、不指定地址 | L6 只出计划；L2 只有纯函数 | `01 §7` 契约测试 + 工具 schema 无地址字段 |
| I1/I2 判定纯函数 + 三条件 | L2 | AST 扫描无 IO/时钟/随机 |
| I10/I11 记忆只增不改、不自我强化 | L5 | `asset_facts` 写入者唯一 + 自检 14/15 |
| **I12（v3.3 新增）** **行动作不产生 verdict，验证动作必须留下可复算依据** | L4 → L2 | `act.*` 的返回类型里没有 verdict 字段；`verify.*` 强制产出 capsule |

---

## 2. 一次任务的九个阶段（生命周期）

```
① 接入      POST /tasks → idem_key 去重 → QUEUED
② 领取      worker 领租约（03 §3）→ LEASED → RUNNING
③ 发现      route_discovery(RouteQuery) → RouteSet（模型不可见、不可干预）
            空/部分空 → 三档降级（08 §1.1）
④ 规划      读 L0 摘要 + 黑板 + knowledge → 产出下一动作
⑤ 门控      guard.binding → guard.preflight（scope/预算/限流/审批）
⑥ 执行      ┌ act.*    → 沙箱内执行，产出 raw trace + 可能的 lead
            └ verify.* → tools.http 发配对请求，产出 Pair
⑦ 判读      verify.*：oracle.judge(pairs) → Judgement（不可变）
            act.*   ：只产 lead，**不判**
⑧ 记账      blackboard_facts / leads / task_events / capsule（只增不改）
            强制闭环检查：有 pending lead → 优先验证它（35 §3）
⑨ 收敛      verdict 全覆盖 / 无新事实 3 步 / 预算到 / 人工停
            → DONE | PAUSED（pause 档）| FAILED | DEAD_LETTER
```

**阶段 ⑦ 是整张图的枢纽**：
`act.*` 走到 ⑦ 时**必须直接落到"记账"**，不能自己去判断"这个是不是洞"。
这条如果破了，就退化成 Strix 那种"模型说这是洞"——不可 replay。

---

## 3. 渗透场景特有的 7 个组件（通用 harness 没有）

| # | 组件 | 通用 harness 有吗 | 我们的落点 | 独特要求 |
|---|---|---|---|---|
| 1 | **路由层** `RouteRef/RouteSet` | ❌ | `27` | 地址运行时解析，类型上写不出 URL |
| 2 | **OOB 外带通道**（DNS/HTTP 回调） | ❌ | `18 ssrf_oob` | 需 listener + nonce，且不能成为 SSRF 出口 |
| 3 | **三条件判定** probe∧baseline∧control | ❌（只判"对不对"） | `07 §0.2` | FP=0 的唯一实现 |
| 4 | **六态 replay**（面没了 ≠ 修好了） | ❌ | `06 §5.1` | 区分"目标下线"与"漏洞修复" |
| 5 | **impact class + 三类审批门** | 部分（有权限无分级） | `05 §1` | 决定"能不能自动打" |
| 6 | **线索账本 leads**（打了但没证的中间态） | ❌ | `35 §2` | 让"探索"与"证明"共处一条时间线 |
| 7 | **目标侧对抗面**（提示注入/响应污染隔离） | ❌ | `05 §9` PC1–PC4 | 渗透是唯一"目标会主动反击 agent"的场景 |

**第 6 项是 v3.3 的关键新增**：没有它，"能打"和"能证明"就只能二选一。
有了它，探索产物有合法归宿（`leads`），不需要强行塞进 `findings`，也不需要另开一个模式。

---

## 4. 扩展矩阵：加一个东西要动哪几处

| 要加的东西 | 必须改的位置 | 批次级成本 | 谁写 |
|---|---|---|---|
| **一类新漏洞（oracle）** | `veripen_oracles/<id>/`（meta/oracle/judge/cases）+ `18 §0` 表 + `18 §6` 标签（若需新标签） | 半天 | 开发 |
| **一个打法（playbook）** | `playbooks/<id>.yaml` + schema 校验 | 1 小时 | **渗透工程师** |
| **一个自由动作（act.\*）** | `veripen_core/actions/registry.py` + 沙箱白名单 + `35 §4` 动作表 | 半天 | 开发 |
| **一种 Transform** | `veripen_oracles/common/harness.py` + `07 §0.4` | 2 小时 | 开发 |
| **一个路由来源** | `veripen_core/routes/sources/<name>.py` | 半天 | 开发 |
| **一段知识（knowledge）** | `knowledge/<topic>.md`（**只进 L2，不进判定**） | 10 分钟 | 渗透工程师 |
| **一个客户档** | `profiles/<name>/*.yaml` | 半天 | 售前 |
| **一种报告框架** | `profiles/<name>/report.yaml`（纯映射） | 2 小时 | 售前 |

**这张表的用途**：面试/评审时被问"加一类洞要多久"，答案不是"看情况"，是上表那一行。
**同时它也是反对"把东西写进内核"的依据**——凡是不在上述位置的新增，都意味着内核被污染了。

---

## 5. 依赖方向（不许反向）

```
api/worker  →  veripen_core  →  （仅数据结构）
                    ↓
        veripen_oracles / veripen_contract
                    ↓
              veripen/report
```

| 规则 | 依据 |
|---|---|
| `veripen_core` **不许 import** `veripen_oracles` | `01 §7` 契约测试 1 |
| `veripen_contract` 不许发网络请求 | `01 §7` + `lint/netimports` |
| `veripen/report` 不许改 verdict | `09`/`17 I4` |
| `actions/`（自由动作）**不许** import `oracle_api` 的判定类型 | **v3.3 新增**（`01 §7` 第八契约） |

最后一条是"能打同时能证明"的架构级保证：
**自由动作看不见判定类型，它在类型上就无法自己下结论。**

---

## 6. 单 agent 循环的形态（为什么不是多进程）

```
while not converged:
    action = plan(L0, blackboard, knowledge, budget)      # L6
    if guard.preflight(action) != ALLOW: continue         # L4 前置门
    if action.kind == "act":        obs = sandbox.run(action)      # 自由
    elif action.kind == "verify":   obs = http.pair(action)        # 参数化
    record(obs)                                            # 黑板 + leads + events
    if obs.produced_leads:     must_verify_next(obs.leads)  # ★ 强制闭环（35 §3）
    if budget.hit(): handle_budget()                       # stop / pause
```

**与 DSH 这类通用 harness 的同构点**：单一会话、单一上下文、工具即动作、逐步推进。
**不同的两点**：
① 动作分两档（自由 vs 验证），档位决定产物类型；
② **`must_verify_next`** 是本项目独有的——通用 harness 没有"线索必须闭环"的概念。

---

## 7. 验收

| # | 断言 | 命令 |
|---|---|---|
| 1 | 八层无反向依赖 | `pytest tests/unit/test_contracts.py -k layers` |
| 2 | 九阶段每阶段落 `task_events` | `make stats TASK=x` 九种 kind 齐全 |
| 3 | 七组件齐全 | `make coverage-matrix` 含组件表 |
| 4 | 扩展矩阵可执行 | 按 §4 每行各做一次演练，只改标注位置 |
| 5 | `act.*` 类型上拿不到 verdict | `test_actions_cannot_import_verdict` |
| 6 | 有 lead 就必须闭环 | `test_pending_lead_forces_verify` |
