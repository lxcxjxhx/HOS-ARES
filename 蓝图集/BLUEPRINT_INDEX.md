# BLUEPRINT_INDEX · 蓝图集索引

> 权威层：**蓝图集**（回答"按什么边界做到什么证据"）。
> 档位：`blueprint-set`，深度 2，直属领域蓝图 ≤5（`17 §2`）。
> 上游：`总设计案.md`。下游：各领域蓝图 + `NEXT.md`。
>
> **渐进提取**：初始化只建索引，**不预建空壳**；做到对应波次时再建领域蓝图文档。

## 1. 五领域蓝图（做了什么才建）

| ID | 领域蓝图 | 覆盖能力 | 覆盖批次 | 建立时点 |
|---|---|---|---|---|
| **BP-CORE** | 内核 / 调度 / guard | C1 C2 C3 C8 | B1 B2 B3 B10 B13 | W1 起逐步建 |
| **BP-PROFILE** | 客户档 | C12 | B16 | W4 建 |
| **BP-EVIDENCE** | 证据 / replay | C4 C9 | B4 B11 | W1 末建 |
| **BP-CAPABILITY** | 14 类 oracle / 契约生成 / 认证 | C5 C6 C7 | B5 B6 B7 B8 B9 B14 | W2 建 |
| **BP-DELIVERY** | 报告 / 离线包 / 控制台 | C11 C12 | B12 B13 B15 B17 | W3 建 |

## 2. 17 批 × 4 波（`21 §1`）

```
W1 内核骨架    B1 骨架+契约  B2 调度  B3 guard  B4 证据
W2 能力扩容    B5 TierA×4   B6 TierA×2  B7 TierB×5  B8 OpenAPI  B9 认证清单
W3 工程交付    B10 agent循环 B11 报告三视图 B12 bench  B13 doctor+离线包+3worker
W4 平台化      B14 TierC×3  B15 Web控制台 B16 demo-b档  B17 交付物料
```

| 波 | 批次 | 退出标准（整波） | 累计断言 |
|---|---|---|---|
| W1 | B1–B4 | 1 个 oracle 端到端跑通 + 证据可 replay | 冷启动门 |
| W2 | B5–B9 | 14 类里 11 类可跑；OpenAPI 生成 200 条用例 | A6 覆盖 |
| W3 | B10–B13 | 三视图 + 44 例 bench + 断网可装 | A3/A4/A5 |
| W4 | B14–B17 | 14 类全绿 + 双档 + 控制台 + 交付物料 | A1/A2/A6/A7 |

## 3. 批次卡（每批 = 一次 agent 运行，DoD 必须可执行）

### W1 · 内核骨架

| # | 批次 | 目标 | 依赖 | 允许改的路径 | DoD |
|---|---|---|---|---|---|
| **B1** | 骨架与契约 | 三契约测试 + models + 3 表 + api | — | `pyproject.toml` `veripen_core/{models,store,oracle_api,errors}.py` `api/` `migrations/` `tests/unit/test_contracts.py` | `pytest tests/unit/test_contracts.py` 3 绿；`curl POST /tasks` 返回 id；两次同 idem_key 同 id；`make lint-nodata-stub` 0 命中 |
| **B2** | 调度 | 租约/心跳/回收/transition | B1 | `veripen_core/{lease,scheduler}.py` `worker/` | `kill -9` worker 后 35s 内任务被接管；`transition` 并发 2 线程只 1 成功 |
| **B3** | guard | scope 三处 + 令牌桶 + 审批骨架 | B1 | `veripen_core/guard/` `tests/unit/test_guard.py` | 缩 scope 后拒绝且零请求；令牌桶 100 并发≈20/s；越界记 `scope ok=0` |
| **B4** | 证据 | capsule seal/chain/replay 四态 | B1 | `veripen_core/evidence.py` `veripen_cli/cmd_replay.py` | 同输入两次 seal 同哈希；改一字节报 TAMPERED；`--offline-fixture` 断网可跑 |

### W2 · 能力扩容

| # | 批次 | 目标 | 依赖 | DoD |
|---|---|---|---|---|
| **B5** | TierA 前 4 类 | sqli_bool_diff / idor_cross_account / ssrf_oob / jwt_replay + OOB listener | B4 | 靶场 4 类各 ≥1 VERIFIED；负样本全 REFUTED；单测 ≥4正+2负/类 |
| **B6** | TierA 余 2 类 | open_redirect / path_traversal | B5 | 各 1 VERIFIED + 2 REFUTED；指纹库命中可解释 |
| **B7** | TierB 5 类 | sqli_error / bola_functional / xss_reflected / mass_assignment / rate_limit | B5 | mass/rate 默认 `enabled:false` 且启动校验生效 |
| **B8** | OpenAPI 生成器 | parser + planner + cases.yaml + cases-diff | B6 | 40 op → 200 用例；两次生成字节一致 |
| **B9** | 认证清单 | JWT J5–J10 变体 + OAuth/mTLS/session 半自动脚本 | B7 | J1–J10 全部有对应用例 |

### W3 · 工程交付

| # | 批次 | 目标 | 依赖 | DoD |
|---|---|---|---|---|
| **B10** | agent 循环 | 双预算/工具 schema/降级链/上下文三层/step.jsonl | B7 | `max_steps=5` 正常收敛；模型名写错→降级 scripted planner 仍完成 |
| **B11** | 报告三视图 | MD/SARIF/JSON + ruleId + fingerprint + verify-manifest | B4,B10 | 三视图 finding 集合相同；VERIFIED 无 capsule → 渲染抛错 |
| **B12** | bench | 44 例 × 3 轮 + 反作弊 | B9,B11 | FP=0；越界=0；三轮 verdict 一致 |
| **B13** | 交付硬化 | doctor 15 项 + 离线包 + 3 worker + 死信 + canary | B12 | 断网 `docker load` 后 `doctor` 全绿 |

### W4 · 平台化

| # | 批次 | 目标 | 依赖 | DoD |
|---|---|---|---|---|
| **B14** | TierC 3 类 | xss_stored_probe / cors_misconfig / graphql_introspection | B7 | 跨会话存储型可判；CORS 预检+GET 双测 |
| **B15** | Web 控制台（只读） | 任务/发现/capsule 浏览 + replay 按钮 | B11 | 只读：无写接口；越权访问返回 403 |
| **B16** | 第二客户档 | `profiles/demo-b`（车云形态）+ demo-alt | B13 | `make demo-alt` 跑通；`git diff veripen_core/` 为空 |
| **B17** | 交付物料 | 演示脚本 + 录屏 + 一页速查 + playbook | B16 | 5 分钟演示一次过；清单 10 项全勾 |

## 4. 依赖图与并行 lane

```
B1 ──┬── B2 ──┐
     ├── B3 ──┤
     └── B4 ──┴── B5 ──┬── B6 ── B8
                       ├── B7 ──┬── B9 ──┐
                       └── B10 ─┤        │
                                ├── B11 ──┼── B12 ── B13 ── B16 ── B17
                                └── B14 ──┘         │
                                          B15 ◀──────┘
```

| lane | 批次 | 独占路径 |
|---|---|---|
| L-CORE | B2, B3, B4, B10 | `veripen_core/` |
| L-ORACLE | B5, B6, B7, B14 | `veripen_oracles/` |
| L-SURFACE | B8, B9, B11, B15 | `veripen_contract/` `checks/` `veripen/report/` `web/` |

**合并点**：B12、B13 必须单线程。**冲突规则**：并行期不许改 `models.py` / `oracle_api.py`。

## 5. Agent 执行协议（每批照做，不许跳）

```
1  读：17 §3 四态 + 本批次卡 + 卡里点名的规格文档（不通读 27 篇）
2  改：只允许动本批次的"产出路径"；越界即停
3  验：跑 DoD 命令，把完整输出贴进批次记录（不许写"应该可以"）
4  阻：真实失败允许开一条临时支线（绑父批次、记触发证据、一层、关闭后回归）
5  记：更新 .veripen/batches.jsonl 一行
6  停：本批次 DoD 全绿即停，不顺手做下一批
```

**禁止**：一次跑多批 / 顺手"优化"已验收代码 / 为过 DoD 放宽断言 / 新增未登记依赖 / 并行期改 `models.py`。

## 6. 上传协议（本项目的 driver 决定 D4）

每批完成后：
1. `git switch -c batch/BX-<slug>` 从 `main` 切出
2. 提交（含 `.veripen/batches.jsonl` 新增行）
3. `git push -u origin batch/BX-<slug>`
4. `gh pr create` → PR 说明含 DoD 输出
5. 合并后 `main` 前进，下批从新 `main` 切

**为什么走 PR 而不是直推**：PR 是可独立抽离的边界——某批出问题，revert 那一个 merge commit 即可，不影响其他批次。

## 7. 进度口径

**完成度 = `done` 批次 / 17，且每批 DoD 有证据。没有证据的 `done` 不算。**

看板只是账本的投影：`make batch-status`。
