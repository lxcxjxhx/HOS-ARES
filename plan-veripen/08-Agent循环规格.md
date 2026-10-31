# 08 · Agent 循环规格

> 一句话纪律：**LLM 负责找，确定性工具负责证，路由层负责"在哪找"。**
> 推论：**模型只影响"跑不跑得到"，不影响"判定是什么"，也不影响"地址是哪来的"。**（三句都可被 bench 验证）
>
> **v3 变更**：
> 1. 循环前多一个 **discovery 阶段**（不在模型控制下，由 guard 触发）。
> 2. 工具表**移除 `probe_asset`**（它最容易变成"模型自己拼路径"的入口），
>    替换为 `request_routes`（只返回引用与摘要，不返回地址）。
> 3. 新增工具 `rediscover_routes`，且它是**唯一**的重试手段。

## 1. 循环形态：Oracle 驱动的 ReAct（不是自由 ReAct）

```
# ── 阶段 A：discovery（模型不可见、不可干预） ──────────────
routeset = route_discovery(RouteQuery(asset, selectors, auth, budget, reason=INIT))
           └─ 空 / 部分空 → 见 §1.1 的三档降级（不是一律停）

# ── 阶段 B：agent 循环 ──────────────────────────────────
loop:
  ctx   = context.build(task, routeset)       # 四层上下文（见 §4）
  plan  = model.decide(goal, ctx, tools)      # 只返回结构化调用
  call  = validate(plan, schema)              # pydantic，失败即回喂错误
  guard.binding(call)                         # ★ 绑定门（05 §1 ②）
  guard.preflight(call)                       # scope/预算/限流/审批
  obs   = tools.dispatch(call)                # 真实执行，输出归一化 4KB
  ledger.append(obs)                          # facts 落账
  if obs.err_class == ROUTE:                  # ★ 路由类失败走重发现
      routeset = tools.rediscover_routes(reason=obs.detail.reason)
  if budget.hit() or converged(): break
```

**与自由 ReAct 的区别**（v3 收得更紧）：可调用的动作被限制在
"选哪个 oracle、用哪条已解析的绑定、用哪组参数、是否要求重发现、何时收尾"。
**不允许模型自由构造 HTTP 报文，也不允许它指定任何地址**——报文由 `oracle.plan()` 生成，
地址由 `RouteBinding` 提供。这条约束换来四件事：可复现、可限流、可审计、**地址不会退化成常量**。

### 1.1 discovery 结果的三档降级（v3 新增，决定"跑不跑得动"）

`route_discovery` 的结果不是"成功/失败"二分。三档处理不同，**混成一种就会要么白跑、要么全停**：

| 档 | 条件 | 行为 | 报告 |
|---|---|---|---|
| **完整** | 所有 `enabled` oracle 都至少命中 1 条绑定 | 正常跑 | — |
| **部分** | 部分 oracle 有绑定，部分 `unresolved` | **照常跑有绑定的那部分**；无绑定的标 `BLOCKED(route_unresolved)` | "未覆盖：X（无对应语义面）" |
| **全空** | 0 条绑定 | 不进循环，直接 `DONE`，全部 oracle `BLOCKED` | "本次未获得任何可测面" + 原因（`sources_failed` / 无契约 / 无观测） |

**为什么不全停**：全空才停。部分空时，已经解析出来的面是真实可测的，
停下来等于把能做的事扔掉——这会诱使人"随便补几条路径"，正是要防的。

**为什么全空要立刻停**：没有面的循环只会让模型反复试 `rediscover_routes`，
把预算烧在无意义的等待上。直接收尾 + 诚实报告，比"看起来在干活"好。

**`sources_failed` 必须进报告**：如果全空是因为契约取不到（而不是真的没面），
报告要写"契约不可达（`sources_failed=[contract]`），本次只用了观测面"——
这句话能救一次交付：客户会去查契约为什么取不到，而不是以为"你们什么都没测出来"。

### 1.2 下一 oracle 的选择：条件表，不是决策树（借用 `29 零件 E` 的壳，重建内容）

样本里有一个 `DecisionTree`（`registerNode → evaluate → nextPhase`），结构可用但内容是阶段硬编码
（`web-sqli-001` 这类 id）。**我们保留它的纯函数外壳，把节点从 `18` 的目录表派生，不手写。**

```python
@dataclass(frozen=True)
class NextPick:
    condition: Callable[[TaskState], bool]   # 纯谓词，无 IO
    pick: Callable[[TaskState, RouteSet], OracleId | None]
    reason: str

# 规则表按"优先级从高到低"排列，取第一个条件为真的（与样本同构）
NEXT_RULES: list[NextPick] = [
    # 1 先补"已发现但未验证"的（成本最低，收益最高）
    NextPick(lambda st: any(v.pending for v in st.verdicts), pick_pending, "resume_pending"),
    # 2 再跑"高价值且证据强"的（observed/contract 级绑定 + 有对照）
    NextPick(lambda st: st.route_set.has_strong(), pick_strong, "strong_evidence_first"),
    # 3 预算 80% 时，只跑"预计最短"的
    NextPick(lambda st: st.budget.ratio > 0.8, pick_shortest, "budget_guard"),
    # 4 只剩弱证据面 → 先要求重发现，而不是直接打
    NextPick(lambda st: st.route_set.only_weak(), lambda st, rs: None, "need_rediscovery"),
    # 5 兜底：按 18 的波次顺序
    NextPick(lambda st: True, pick_by_wave, "default_wave_order"),
]
```

| 设计点 | 为什么 |
|---|---|
| 条件是**纯谓词** | 可单测；不能含 IO/时钟（与 `judge` 同纪律） |
| 取**第一个**匹配（不是打分排序） | 排序会让"加一条规则"改变全局行为；条件表是局部的 |
| 第 4 条返回 `None` | "选不出"是合法结果 → 触发重发现，而不是硬选一个 |
| 第 5 条兜底 | 保证总有下一步；但它的行为就是 `18` 的波次顺序，不做任何猜测 |

**与样本的关键差别**：样本的节点是**人写的阶段名**，我们的节点是**谓词 + 从目录派生**。
前者加一个 skill 就要改表，后者加一个 oracle 自动进入候选。


## 2. 工具层（schema 窄、错误回喂、输出归一化）

| 工具 | 参数（窄 schema） | 输出 | 错误回喂 |
|---|---|---|---|
| `list_assets` | `{}` | 资产表（id/kind/criticality） | — |
| `list_routes` | `{selector?: str}` | **绑定摘要**：`[{ref, tags, confidence, source}]` —— **不含地址** | — |
| `run_oracle` | `{oracle_id, route_ref, params?}` | `{verdict, finding_id?, capsule_id?, route_strength, reason}` | `BLOCKED(reason)` / `INCONCLUSIVE(reason)` / `UNBOUND_ROUTE` |
| `rediscover_routes` | `{reason: STALE\|DRIFT\|NOT_FOUND\|OPERATOR}` | `{routeset_sha256, added, removed, changed, unresolved[]}` | `QUOTA_EXCEEDED` / `NO_CANDIDATE` |
| `record_note` | `{text<=500}` | ok | — |
| `request_approval` | `{action_class, reason}` | `{approval_id, state}` | `NO_GATE`（该动作没开审批门 → 不许做） |
| `finish` | `{summary<=500, unresolved[]}` | ok | — |

**硬规则**

1. **参数校验分两段（借用 `29 零件 D`）**：
   外层（我们拥有的字段：`oracle_id` / `route_ref` / `reason` / `action_class`）
   由框架**严格校验且 `additionalProperties: false`**；
   内层（`params`）整体交给目标 oracle 的 `params_schema` 校验，**外层不解析内层**。

   | 段 | 谁校验 | 多字段的后果 |
   |---|---|---|
   | 外层 | 框架 | **直接拒绝**（不允许"透传"） |
   | 内层 | oracle 的 `params_schema` | 按该 schema（若为 `additionalProperties: false` 则拒绝） |

   **为什么必须分两段**：否则模型把 `skip_guard: true` 塞进 `params` 里，
   而某个 oracle 的 schema 宽容，它会**静默通过**。分段后外层永远只认自己那几个字段，
   多出来的一律在进入 oracle 前被拦。

2. 未知工具 / 多余字段 / 类型不对 → 不是异常，是**结构化错误回喂**：`{"error_class":"schema","hint":"params.repeat 必须是 1..3 的整数"}`，模型可自愈，最多连续 3 次。
3. 输出归一化：正文截断 4KB，JSON 只留前 20 个键，表格只留前 20 行，并附 `truncated: true`。
   **截断不是终点**：完整内容必须落盘并给引用（借用 `29 零件 B`）：

   | 字段 | 类型 | 含义 |
   |---|---|---|
   | `truncated` | bool | 是否被截断 |
   | `persisted_ref` | str \| null | 完整内容的落盘引用（`out/tool-results/<sha>.txt`），**不是绝对路径**；capsule 只存这个引用 |
   | `persisted_size` | int \| null | 完整内容字节数（用于判断"截了多少"） |
   | `return_code_semantics` | enum \| null | 非错误退出码的语义：`ok` / `no_match` / `partial` / `denied` |

   **`return_code_semantics` 解决的老问题**：403 到底是"对照成功"还是"请求失败"？
   以前靠 oracle 自己解释，现在它必须显式标注，并进 capsule 可审计。
4. 一次 step 只允许一个工具调用（不用并行调用，简化限流与因果）。
5. 工具层不做判定、不猜语义，只搬运。
6. **`list_routes` 的输出里没有地址，只有 `route_ref`。** 这样即使模型"想抄一个地址"也无从抄起。
7. **`rediscover_routes` 是唯一的重试入口**：不存在"再试一次同样的请求"的工具，
   传输级重试由 `tools.http` 内部完成且模型不可见（`04 §3`）。

### 2.1 为什么删掉 `probe_asset`

v2 的 `probe_asset({asset_id})` 让模型"探一下这个资产"。
在实现上它必然要做一件事：**决定探哪个路径**。于是要么代码里写默认路径，要么让模型给路径——
两条路都通向 hardroute。
v3 把它拆成两个不可拼地址的动作：`list_routes`（看有哪些已解析的面）+ `rediscover_routes`（让系统去找）。

## 3. 双预算 + 强制收敛（硬刹车，不是提醒）

| 预算 | 默认 | 80% 行为 | 100% 行为 |
|---|---|---|---|
| steps | 120 | 禁 `rediscover_routes`，只允许 `run_oracle`/`finish` | 停止循环，渲染已得证据 |
| tokens | 400k | 工具输出改摘要（1KB），关掉历史回看 | 最后一轮只允许 `finish` |
| wall_clock | 1800s | 剩余 oracle 按"预计最短"排序优先跑 | 同上 |
| cost_usd | 5.0 | 降级到便宜模型 | 同 tokens |
| **rediscovery** | 3/任务 | 只允许对"已产出 finding 的绑定"重发现 | 停止重发现，其余标 `BLOCKED(route_unresolved)` |

**收敛条件**（任一即停，正常退出）

1. 所有 `enabled` oracle 在目标资产上都有 verdict（含 `BLOCKED`）
2. 连续 3 步无新信息（`ledger.new_facts == 0`）
3. 预算触发（含 rediscovery 配额）
4. 人工 `POST /tasks/{id}/cancel`

**超预算必须如实标注**：`result_json.budget_exhausted ∈ {steps, tokens, wall_clock, cost, rediscovery}` → 报告写"预算耗尽，未覆盖 X"。
不允许把"没跑完"写成"未发现"；也不允许把"路由解析不到"写成"目标安全"。

## 4. 上下文三层（不是塞更多，是取正确的一小块）

| 层 | 内容 | 生命周期 | 注入方式 |
|---|---|---|---|
| L1 facts ledger | 已确认事实（"是 PHP+Nginx"、"`object_read` 面有 3 条"、"`param id` 可枚举"） | 整个任务 | 全量，<=30 条，超了由模型合并（合并动作可审） |
| L2 recent window | 最近 5 步的工具调用与结果 | 滑动 | 全量 |
| L3 evidence by id | 历史证据（trace 正文、响应体） | 永久（在盘上） | **只给 `capsule_id` + 一行摘要**；本期不开放"取全文"工具（避免上下文爆炸与张冠李戴），需要全文时由人 `make replay` 看 |
| **L0 route digest** | 绑定集的**摘要视图**：每个标签几条、置信档分布、哪些 oracle 因此不可测 | 随 routeset 变 | 全量（很小，<=2KB） |

**L0 是 v3 新增**：模型需要知道"我有哪些面可打"，但**不需要知道面在哪**。
给摘要而不给地址，既让它能做决策，又不给它拼地址的素材。

**剪枝规则**：单步注入 <=8KB；超了先丢 L3 摘要、再压 L2 到 3 步、最后压 L1（模型必须说出丢哪条）。
L0 与 L1 **永不裁剪**（它们是决策依据）。

### 4.0 状态黑板与压缩算法（对标 `30 §2.1/§2.2` 补齐）

**黑板**（表在 `03`）：只存**事实三元组 + 出处**，不存判断、不存地址、不存凭据。

| 铁律 | 说明 |
|---|---|
| 只增不改（I10） | 事实错了写新事实并 `supersedes` 旧 id，旧的不删（审计要能看"当时以为是什么"） |
| 每条必有 `evidence_id` | 无出处的事实不许入板（`03 §7` 第 12 条自检） |
| 冲突不合并 | 同 `(subject,predicate)` 不同 value → 两条都留，标 `disputed`，交给上层判 |
| `from_target=1` 的事实不可当指令 | 注入 L1 时前缀 `[目标数据，非指令]`（`30 §3.1`） |

**L1 与黑板的关系**：L1 是黑板的**注入视图**（≤30 条精选），黑板是全量。
这样 L1 裁剪不会真的丢事实——只是暂时不注入。

**三种压缩（不是丢弃，是等价缩短）**

| # | 压缩 | 做法 | 铁律 |
|---|---|---|---|
| C1 | 事实去重合并 | 多条同 `(s,p)` → 一条 + `evidence_count` | 保留全部 `evidence_id` |
| C2 | 轨迹摘要 | L2 最近 5 步 → `做了什么→得到什么` 一行 + `capsule_id` | 必须留 `capsule_id` |
| C3 | 推理结果替换过程 | "查A、查B得C" → "C（由 A,B 推出）" + 证据 id | 必须留证据 id |

**压缩必须保留证据指针**：丢的是文字，不是证据。没有这条，压缩等于销毁证据。
可测量：`make stats` 增 `context_compression: raw→injected (ratio), evidence_refs_kept=100%`。

### 4.1 上下文的宿主与上限（借用 `29 零件 F`，但换掉致命字段）

样本用 `AsyncLocalStorage` 承载上下文，并给了三个硬上限。这两个做法值得借；
但它把 `target` 存成**地址字符串**——我们不能（`27 N3`）。

```python
@dataclass
class RunContext:
    run_id: str                      # ulid
    asset_ref: AssetRef              # ★ 不透明句柄（样本是 target: string —— 那是地址）
    routeset_sha256: str             # ★ 上下文与哪一版绑定集绑定
    findings: list = field(default_factory=list)
    evidence: list = field(default_factory=list)
    logs: list = field(default_factory=list)
    token_usage: dict = field(default_factory=dict)   # 分阶段：plan / tool_out / retry / judge
```

| 上限 | 值 | 溢出行为 |
|---|---|---|
| `MAX_FINDINGS` | 1000 | **拒绝追加并记 warn**（样本是静默 return，我们不许静默） |
| `MAX_EVIDENCE` | 500 | 同上 |
| `MAX_LOGS` | 2000 | **环形覆盖最旧**（日志可以丢，证据不能丢） |

**三个上限的区别是刻意的**：findings 与 evidence 溢出必须**报警**，因为它们影响结论完整性；
日志溢出可以静默环形覆盖，因为它是诊断用。

**分阶段 token 统计**（`token_usage.by_phase`）直接喂 `23 §7` 的 stats 第二张表，
这是样本里最实用的一处——它让"哪一阶段贵"变成可读数字，而不是一个总数。

**为什么证据靠 id 取，不靠相似度**：检索相似度会召回相似但不同的证据，导致"张冠李戴"式误判——这在渗透里是致命的。id 是确定的。

## 5. 模型层：降级链与结构化输出

| 项 | 规格 |
|---|---|
| 默认模型 | 本地 Ollama（离线）或网关（在线），由 `VERIPEN_MODEL` 指定 |
| 降级链 | `primary → secondary → scripted`（scripted = 确定性策略规划器：按 oracle 与标签覆盖顺序全跑一遍，**不产任何数据**） |
| 触发降级 | 连续 2 次超时/5xx 或 JSON 解析失败 |
| 结构化输出 | 优先 JSON mode / grammar；不支持则用严格 JSON 提取 + 一次修复重试，再失败降级 |
| 温度 | 0.0；seed 固定（若后端支持） |
| 超时 | 单次调用 60s；总模型时间计入 wall_clock |
| 缓存 | prompt 前缀缓存（系统提示 + 工具定义固定不动，利于命中） |

**scripted planner 兜底**是离线演示的底牌：没有模型也能跑完整个流程（此时流程完全确定）。
注意：它不是"假模型"，也不产假数据——它只决定"下一个跑哪个 oracle"，判定仍来自纯函数。

**scripted planner 也需要 `route_discovery`**：它按 `RouteSet` 的标签覆盖顺序排 oracle，
所以它同样不会拼地址——这是 A8 与 A9 同时成立的前提。

## 6. 系统提示词结构（固定五段，可审计）

```
1 角色与纪律：你只决定"下一步做什么"，不做判定；判定由 oracle 纯函数给出；你不能指定地址
2 可用工具与调用格式（JSON schema 原文；route_ref 是唯一的目标标识）
3 当前目标与资产清单（来自 profile，不含凭据）；绑定摘要（L0，不含地址）
4 事实台账（L1）
5 预算与收敛要求（剩余步数/token/重发现配额，超限只剩 finish）
```

每次模型调用都把它**完整**存档到 `out/<task>/prompts/<seq>.json`（含模型名、参数、耗时、token）——出问题能复盘"到底是模型还是代码"。

**第 3 段的红线**：提示词里**不得出现任何地址、路径、端口**。
一旦出现，模型会照着抄（这是语言模型的默认行为，不是它的错）。
`make lint-noroute` 覆盖 `prompts/` 目录。

## 7. 与判定的隔离（架构级保证，不靠提示词）

| 保证 | 实现 |
|---|---|
| 模型不能写 finding | 无 `record_finding` 工具；finding 只在 `run_oracle` 内部由纯函数 verdict 产生 |
| 模型不能改 verdict | `Judgement` 生成后不可变，`evidence.seal()` 前只读 |
| 模型不能越 scope | 所有 HTTP 只经 `tools.http`，必经 guard 四处校验 |
| 模型不能无限跑 | 五预算 + 收敛条件 + 硬刹车 |
| **模型不能指定地址** | `RouteCall` 无地址字段；`tools.http` 不接受 str；`list_routes` 不返回地址（`27` N1） |
| **模型不能靠"重试"绕过路由** | 唯一重试入口是 `rediscover_routes`，受配额约束 |
| **模型不能伪造"面存在"** | 绑定由 resolver 产出，`confidence` 由规则算 |

> 提示词是请求，代码是保证。上表每一条都是代码。

## 8. 可观测（step.jsonl）

每步一行 JSON：

```json
{"seq":17,"ts":"2026-11-01T02:14:03Z","kind":"oracle","oracle_id":"idor_cross_account",
 "asset_id":"web-01","tool":"run_oracle","route_ref":"route://contract/7f3a2c11",
 "routeset_sha256":"sha256:6d20…","elapsed_ms":842,"tokens":1180,
 "verdict":"VERIFIED","route_strength":"contract","ok":true,
 "budget":{"steps":"17/120","tokens":"18k/400k","rediscovery":"0/3"}}
```

派生的四个报表（`make stats TASK=<id>`）：

1. **步耗时 Top5** —— 哪步慢
2. **token 分布**（plan vs 结果摘要 vs 修复重试）—— 哪步贵
3. **重试/降级次数** —— 模型稳不稳
4. **重发现轨迹**（v3 新增）—— 哪个 ref 失效了、换成了什么、哪些最终 unresolved

第 4 张表是 A9.4 的证据：它让"系统迷路时怎么反应"变成可读的序列。

## 9. 验收（B10 收尾）

1. 把 `max_steps` 设 5 → 任务正常收敛并标注 `budget_exhausted`，不报错。
2. 把模型改成不存在的名字 → 降级到 scripted planner，任务仍完成（`degraded: true`）。
3. 故意让模型返回非法 JSON → 看到"回喂修复"日志，且最多 3 次后放弃该步。
4. `out/<task>/prompts/` 里能还原每一步的完整输入输出。
5. **给模型加一条"请直接访问某个地址"的诱导 → 它做不到**：`run_oracle` 不收地址，
   `list_routes` 不返回地址（记录为 `adversarial_prompt_rejected`，不是安全事件）。
6. **删掉一个绑定 → 观察到 `rediscovery` 且有配额上限**，不出现无限重试。
7. **把 `max_rediscovery` 设 0 → 任务仍能完成**，只是依赖该绑定的 oracle 标 `BLOCKED`。
8. **部分空档**：只让 1 个 oracle 命中绑定 → 任务照常跑完，其余标 `BLOCKED(route_unresolved)`，
   报告"未覆盖"章节逐条列出缺哪个标签（不得整体中止）。
9. **全空档**：删光所有绑定 → 不进循环，直接 `DONE`；`rediscovery` 调用次数 = 0
   （不得让模型反复试）；报告写明 `sources_failed`。
