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

## 1. 总览：4 波 18 批

```
W1 内核骨架    B1 骨架+契约  B1.5 路由层  B2 调度  B3 guard  B4 证据
W2 能力扩容    B5 TierA×4   B6 TierA×2  B7 TierB×5  B8 契约解析  B9 认证清单
W3 工程交付    B10 agent循环 B11 报告三视图 B12 bench  B13 doctor+离线包+3worker
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
| **B1** | 骨架与契约 | 六契约测试 + models + 3 表 + api | — | `pyproject.toml` `docker-compose.yml` `veripen_core/{models,store}.py` `api/` `config/bindings/service.yaml` `tests/unit/test_contracts.py` | `pytest tests/unit/test_contracts.py` **6 绿**；`make bindings-check` 通过；两次同 idem_key 同 id；`make lint-noroute` 0 命中；`make lint-nodata-stub` 0 命中 |
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
| **B10** | agent 循环 | 五预算/工具 schema（含 `list_routes`/`rediscover_routes`）/降级链/上下文四层/step.jsonl | B7, B8 | `veripen_core/{budget,context}.py` `agent/` `tools/` | `max_steps=5` 正常收敛并标 budget_exhausted；模型名写错→降级 scripted planner 仍完成；非法 JSON 回喂 ≤3 次；**给模型"直接访问某地址"的诱导 → 无法执行**；`max_rediscovery=0` 仍能完成 |
| **B11** | 报告三视图 | MD/SARIF/JSON + ruleId + 双指纹 + verify-manifest + 措辞 lint + **弱绑定降级** | B4, B10 | `veripen/report/` `veripen_cli/cmd_report.py` | 三视图 finding 集合相同；VERIFIED 无 capsule → 渲染抛错；**弱绑定+high → 渲染抛错**；**三视图零地址**；手改报告 → manifest 校验失败 |
| **B12** | bench | 44 例 × 3 轮 + 反作弊 + **routeset 一致性** | B9, B11 | `tests/bench/cases.yaml` `veripen_cli/cmd_bench.py` | `make data-verify` + `make range-selftest` 先绿；FP=0；越界=0；三轮 verdict 一致；**三轮 routeset_sha256 一致**；**渲染结果可由 routeset 推出**；`bench.md` 含未命中用例（分两类）与数据 sha256 |
| **B13** | 交付硬化 | doctor 16 项 + 离线包 + 3 worker + 死信 + canary | B12 | `veripen_cli/cmd_doctor.py` `scripts/package-offline.sh` `Makefile` | 断网 `docker load` 后 `doctor` 全绿（含 `data-verify`、`bindings-check`、路由卫生 5 条）；离线包含 `data/` 快照与 manifest；3 worker 并行不重不漏；死信单列 |

### W4 · 平台化

| # | 批次 | 目标 | 依赖 | 产出 | DoD |
|---|---|---|---|---|---|
| **B14** | TierC 3 类 | xss_stored_probe / cors_misconfig / graphql_introspection | B7 | `veripen_oracles/{3 个}/` | 跨会话存储型可判；CORS 预检+GET 双测；GraphQL 单绑定多 operation 表达正确 |
| **B15** | Web 控制台（只读） | 任务/发现/capsule/绑定浏览 + replay 按钮 | B11 | `web/`（FastAPI + 单页静态） | 只读：`ui.*` 写操作 405；replay 按钮调 CLI 等价 op；越权访问 403；**绑定页只显示摘要，无模板正文** |
| **B16** | 第二客户档 | `profiles/demo-b`（车云形态）+ demo-alt | B13 | `profiles/demo-b/` | `make demo-alt` 跑通；`git diff --stat veripen_core/` 为空；`lint-noleak` 通过；**`lint-noroute` 通过**；两个档的 selectors 差异只在 YAML |
| **B17** | 交付物料 | 演示脚本 + 录屏 + 一页速查 + playbook + 分发清单 | B16 | `docs/delivery/` `scripts/demo.sh` | 5 分钟演示一次过（含"删绑定重发现"这一段）；断网兜底可跑；清单 10 项全勾 |

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
| B17 | `22 §7`、`12 §9`、`24 §5` | 200 | 删 `docs/delivery/` | 演示超时 → 用 `demo-offline` 兜底 |

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
1  读：17 §0 九条不变量 + 17 §3 四态账本 + 本批次卡 + 卡里点名的规格文档（不要通读 28 篇）
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
纯 agent 无人值守跑 18 批的失败率接近 100%，因为没有中间证据。

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
W4 ▢B14 ▢B15 ▢B16 ▢B17          → A1/A2/A6/A7
```

每完成一批，把 ▢ 改 ▣，并在 `.veripen/batches.jsonl` 追加一行。**账本是唯一事实，看板只是它的投影。**

## 9. 与 17 的关系

`17` 定"权威与四态"，本文定"怎么一批批吃掉全量"。
开工前需要 `17 §6` 的 driver 确认；确认后从 `B1` 开始，一次只跑一批。
