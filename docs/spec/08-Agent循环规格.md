# 08 · Agent 循环规格

> 一句话纪律：**LLM 负责找，确定性工具负责证。**
> 推论：**模型只影响"跑不跑得到"，不影响"判定是什么"。**（这句话可被 bench 验证，见 10）

## 1. 循环形态：Oracle 驱动的 ReAct（不是自由 ReAct）

```
loop:
  ctx   = context.build(task)                 # 三层上下文（见 §4）
  plan  = model.decide(goal, ctx, tools)      # 只返回结构化调用
  call  = validate(plan, schema)              # pydantic，失败即回喂错误
  guard.preflight(call)                       # scope/预算/限流/审批
  obs   = tools.dispatch(call)                # 真实执行，输出归一化 4KB
  ledger.append(obs)                          # facts 落账
  if budget.hit() or converged(): break
```

**与自由 ReAct 的区别**：可调用的动作被限制在"选哪个 oracle、在哪个资产、用哪组参数、何时收尾"。
不允许模型自由构造 HTTP 报文——报文由 `oracle.plan()` 生成，模型只能选 oracle 与参数。

这条约束换来三件事：可复现、可限流、可审计。**这是整套设计里第二重要的决定。**（第一是判定纯函数）

## 2. 工具层（schema 窄、错误回喂、输出归一化）

| 工具 | 参数（窄 schema） | 输出 | 错误回喂 |
|---|---|---|---|
| `list_assets` | `{}` | 资产表（id/kind/criticality） | — |
| `probe_asset` | `{asset_id}` | 指纹摘要（状态、server、tech 猜测、参数清单） | `TIMEOUT/RESET` 分类 + 建议 |
| `run_oracle` | `{oracle_id, asset_id, params?}` | `{verdict, finding_id?, capsule_id?, reason}` | `BLOCKED(reason)` / `INCONCLUSIVE(reason)` |
| `record_note` | `{text<=500}` | ok | — |
| `request_approval` | `{action_class, reason}` | `{approval_id, state}` | `NO_GATE`（该动作没开审批门 → 不许做） |
| `finish` | `{summary<=500, unresolved[]}` | ok | — |

**硬规则**

1. 未知工具 / 多余字段 / 类型不对 → 不是异常，是**结构化错误回喂**：`{"error_class":"schema","hint":"params.repeat 必须是 1..3 的整数"}`，模型可自愈，最多连续 3 次。
2. 输出归一化：正文截断 4KB，JSON 只留前 20 个键，表格只留前 20 行，并附 `truncated: true`。
3. 一次 step 只允许一个工具调用（不用并行调用，简化限流与因果）。
4. 工具层不做判定、不猜语义，只搬运。

## 3. 双预算 + 强制收敛（硬刹车，不是提醒）

| 预算 | 默认 | 80% 行为 | 100% 行为 |
|---|---|---|---|
| steps | 120 | 禁 `probe_asset`，只允许 `run_oracle`/`finish` | 停止循环，渲染已得证据 |
| tokens | 400k | 工具输出改摘要（1KB），关掉历史回看 | 最后一轮只允许 `finish` |
| wall_clock | 1800s | 剩余 oracle 按"预计最短"排序优先跑 | 同上 |
| cost_usd | 5.0 | 降级到便宜模型 | 同 tokens |

**收敛条件**（任一即停，正常退出）

1. 所有 `enabled` oracle 在目标资产上都有 verdict
2. 连续 3 步无新信息（`ledger.new_facts == 0`）
3. 预算触发
4. 人工 `POST /tasks/{id}/cancel`

**超预算必须如实标注**：`result_json.budget_exhausted = "steps"` → 报告写"预算耗尽，未覆盖 X"。
不允许把"没跑完"写成"未发现"。

## 4. 上下文三层（不是塞更多，是取正确的一小块）

| 层 | 内容 | 生命周期 | 注入方式 |
|---|---|---|---|
| L1 facts ledger | 已确认事实（"web-01 是 PHP+Nginx"、"参数 id 可枚举"） | 整个任务 | 全量，<=30 条，超了由模型合并（合并动作可审） |
| L2 recent window | 最近 5 步的工具调用与结果 | 滑动 | 全量 |
| L3 evidence by id | 历史证据（trace 正文、响应体） | 永久（在盘上） | **只给 `capsule_id` + 一行摘要**；本期不开放"取全文"工具（避免上下文爆炸与张冠李戴），需要全文时由人 `make replay` 看 |

**剪枝规则**：单步注入 <=8KB；超了先丢 L3 摘要、再压 L2 到 3 步、最后压 L1（模型必须说出丢哪条）。

**为什么证据靠 id 取，不靠相似度**：检索相似度会召回相似但不同的证据，导致"张冠李戴"式误判——这在渗透里是致命的。id 是确定的。

## 5. 模型层：降级链与结构化输出

| 项 | 规格 |
|---|---|
| 默认模型 | 本地 Ollama（离线）或网关（在线），由 `VERIPEN_MODEL` 指定 |
| 降级链 | `primary → secondary → scripted`（scripted = 确定性策略规划器：按 oracle 顺序全跑一遍，**不产任何数据**） |
| 触发降级 | 连续 2 次超时/5xx 或 JSON 解析失败 |
| 结构化输出 | 优先 JSON mode / grammar；不支持则用严格 JSON 提取 + 一次修复重试，再失败降级 |
| 温度 | 0.0；seed 固定（若后端支持） |
| 超时 | 单次调用 60s；总模型时间计入 wall_clock |
| 缓存 | prompt 前缀缓存（系统提示 + 工具定义固定不动，利于命中） |

**scripted planner 兜底**是离线演示的底牌：没有模型也能跑完整个流程（此时流程完全确定）。
注意：它不是"假模型"，也不产假数据——它只决定"下一个跑哪个 oracle"，判定仍来自纯函数。

## 6. 系统提示词结构（固定五段，可审计）

```
1 角色与纪律：你只决定"下一步做什么"，不做判定；判定由 oracle 纯函数给出
2 可用工具与调用格式（JSON schema 原文）
3 当前目标与资产清单（来自 profile，不含凭据）
4 事实台账（L1）
5 预算与收敛要求（剩余步数/token，超限只剩 finish）
```

每次模型调用都把它**完整**存档到 `out/<task>/prompts/<seq>.json`（含模型名、参数、耗时、token）——出问题能复盘"到底是模型还是代码"。

## 7. 与判定的隔离（架构级保证，不靠提示词）

| 保证 | 实现 |
|---|---|
| 模型不能写 finding | 无 `record_finding` 工具；finding 只在 `run_oracle` 内部由纯函数 verdict 产生 |
| 模型不能改 verdict | `Judgement` 生成后不可变，`evidence.seal()` 前只读 |
| 模型不能越 scope | 所有 HTTP 只经 `tools.http`，必经 guard 三处校验 |
| 模型不能无限跑 | 四预算 + 收敛条件 + 硬刹车 |

> 提示词是请求，代码是保证。上表每一条都是代码。

## 8. 可观测（step.jsonl）

每步一行 JSON：

```json
{"seq":17,"ts":"2026-11-01T02:14:03Z","kind":"oracle","oracle_id":"idor_cross_account",
 "asset_id":"web-01","tool":"run_oracle","elapsed_ms":842,"tokens":1180,
 "verdict":"VERIFIED","ok":true,"budget":{"steps":"17/120","tokens":"18k/400k"}}
```

派生的三个报表（`make stats TASK=<id>`）：

1. **步耗时 Top5** —— 哪步慢
2. **token 分布**（plan vs 结果摘要 vs 修复重试）—— 哪步贵
3. **重试/降级次数** —— 模型稳不稳

## 9. 验收（D2 收尾）

1. 把 `max_steps` 设 5 → 任务正常收敛并标注 `budget_exhausted`，不报错。
2. 把模型改成不存在的名字 → 降级到 scripted planner，任务仍完成（`degraded: true`）。
3. 故意让模型返回非法 JSON → 看到"回喂修复"日志，且最多 3 次后放弃该步。
4. `out/<task>/prompts/` 里能还原每一步的完整输入输出。
