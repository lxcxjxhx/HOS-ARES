# 31 · Playbook 规格（打法资产）

> **oracle 是公理，playbook 是定理。**
> oracle 回答"怎么判"，playbook 回答"怎么打"——顺序、前置、坑、失败模式。
>
> 为什么单独立章：`18` 给了 14 个判定器，但**判定器不会自己走到正确的位置**。
> 同一个 `idor_cross_account`，配不配 playbook，成功率差数倍。
> 而客户买的从来不是"你有几个判定器"，是"**你知道怎么打**"。

## 0. 三条边界（先划清，否则会变成第二个 oracle）

| # | 边界 | 反面 |
|---|---|---|
| P1 | **playbook 只能调 oracle，不能改变判定** | 在 playbook 里写"如果 403 也算通过" → 破坏 I2 |
| P2 | **playbook 不含地址**（`27` N3） | 写"打 /api/users/1" → 就是硬路由 |
| P3 | **playbook 不写自由文本 prompt** | 写一大段"你要小心地……" → 那是提示词，不是资产 |

**P3 是最容易犯的**：一写 playbook 就想写"建议 agent 谨慎行事"。
那不是资产，那是指令。playbook 的每一步必须是**可枚举的动作 + 可检查的产出**。

## 1. 判定它是"资产"还是"笔记"的三条标准

| 标准 | 资产 | 笔记 |
|---|---|---|
| 可执行 | 每步是 `action` 枚举值 | 每步是自然语言 |
| 可验证 | 每步 `produces` 是 fact 或 verdict | "看看有没有洞" |
| 可度量 | 有 `success_rate`（bench 实测） | "效果不错" |

**没有 `success_rate` 的 playbook 不许对外称"资产"**（`30 §2.3`）。
这一条很硬：它逼我们真的跑 bench，而不是把经验写成文档就当资产卖。

## 2. 完整格式（`veripen.playbook/v1`）

```yaml
apiVersion: veripen.playbook/v1
id: pb-idor-dual-account              # 稳定 id，进 bench 结果
version: 1.0.0
title: 双账号对象级越权（BOLA）
oracle: idor_cross_account            # 服务哪个判定器（适用关系，非绑定）
impact: read_only

applies_when:                         # 全部满足才选中；任一不满足 → SKIPPED，不是失败
  - selector_available: object_read
  - auth_available: [primary, secondary]
  - min_route_confidence: 0.85

steps:
  - id: s1
    action: verify_accounts            # 枚举动作，不是自由文本
    params: {roles: [primary, secondary]}
    produces: [fact:auth.primary.usable, fact:auth.secondary.usable]
    on_fail: abort(blocked_no_auth)    # 中止条件必须显式

  - id: s2
    action: sample_object_ids          # 从 object_list 采样真实 id
    params: {via: object_list, count: 5}
    produces: [fact:object.ids.sample]
    on_empty: abort(skipped_no_selector_match)

  - id: s3
    action: run_oracle
    params: {oracle: idor_cross_account, selector: object_read, id_source: s2.sample}
    produces: [verdict]
    on_verdict:
      VERIFIED:    {next: s4}
      REFUTED:     {next: end}
      INCONCLUSIVE:{next: s4}          # 补一次对照再判

  - id: s4
    action: recheck_with_control       # 用另一条绑定当对照
    produces: [verdict, fact:control.denied]
    on_fail: record(inconclusive_no_control)

pitfalls:                              # 人写的失败模式，最有价值的部分
  - id: pf1
    text: "用 1..50 枚举对象 id 会触发风控；应先从列表接口取真实 id"
    guard: use_sampled_ids             # 指向具体步骤的约束，不是空话
  - id: pf2
    text: "若 control 也 200，说明该面是公开资源，必须 REFUTED（N-03 的教训）"
    guard: require_control_denied

rollback: none                         # read_only → none；写类必须给回滚步骤
success_rate: null                     # bench 实测后填；null = 未测，不许对外称资产
```

## 3. `action` 枚举（封闭集合，不许自由发挥）

| action | 语义 | 产出类型 |
|---|---|---|
| `verify_accounts` | 确认角色可用（登录/令牌） | `fact:auth.<role>.usable` |
| `sample_object_ids` | 从列表面采样真实对象 id | `fact:object.ids.sample` |
| `discover_token` | 获取令牌（走 `token_issue` 绑定） | `fact:token.<role>.ref` |
| `run_oracle` | 调一个 oracle | `verdict` |
| `recheck_with_control` | 用另一条绑定做对照再判 | `verdict` + `fact:control.*` |
| `probe_shape` | 探测参数形状（受 `discovery_probe` 门） | `fact:param.shape` |
| `record_inconclusive` | 记"判不了"及原因 | `verdict(INCONCLUSIVE)` |
| `abort` | 中止该 playbook | — |

**新增 action 的条件**：现有 8 个**表达不了**，且在 ≥2 个 playbook 里复用。
只为单个 playbook 服务的特殊动作，用 `run_oracle` + 参数表达，不要新增。

**为什么封闭**：开放的 action 集合会退化成"小脚本"，
而脚本无法被 bench 度量、无法被 audit、无法被 review。**资产必须是有限词汇表。**

## 4. `applies_when` 的判定（选中 ≠ 执行）

| 情形 | 结果 | 计入 |
|---|---|---|
| 全部满足 | 选中，执行 | TP/FP 分母 |
| 任一不满足 | `SKIPPED(not_applicable)` | **不进分母**，进"未覆盖" |
| 满足但执行中 abort | 按 abort 原因：`blocked_*` / `skipped_*` | 不进分母 |

**为什么"不满足"不算失败**：没有双账号就不能测 BOLA，这是**环境限制**不是**能力缺陷**。
把它算成 FN 会让 recall 失真（`10 §3` 的 `SKIPPED` 分列就是这个道理）。

## 5. 与 profile 的关系

```yaml
# profile 里
oracles:
  enabled: [idor_cross_account]
playbooks:                       # ★ 新增：选打法，可多个，按顺序尝试
  - pb-idor-dual-account
  - pb-idor-admin-scope          # 若第一个 SKIPPED，试第二个
  strict: false                  # false=允许无 playbook 直接跑 oracle（退化模式）
```

| 配置 | 行为 |
|---|---|
| `playbooks: [...]` | 按顺序尝试，第一个 `applies_when` 满足的执行 |
| `playbooks: []` + `strict: false` | 无 playbook，直接 `run_oracle`（`19` 的默认行为） |
| `playbooks: []` + `strict: true` | **启动即失败**（要求必须配打法） |

**为什么默认 `strict: false`**：本期 playbook 数量少，强制会让很多 oracle 跑不了。
但**交付给客户时建议 `strict: true`**——那才是"资产化"的形态。

## 6. 校验（schema + 三条语义检查）

`schemas/playbook.schema.json` 管结构，另有三条 schema 管不了的：

| # | 检查 | 失败 |
|---|---|---|
| 1 | `steps[].params.selector` / `id_source` 引用的 id 必须存在于前序步骤的 `produces` | 启动失败 |
| 2 | `on_verdict` / `on_fail` 的 `next` 必须是已存在的 step id 或 `end` | 启动失败 |
| 3 | `oracle` 必须在 `18` 目录里存在，且 `impact` 与 oracle 的 impact 一致 | 启动失败 |

**第 3 条防的是**：playbook 声称 `read_only`，但它调的 oracle 是 `reversible_write`。
那会让审批门被绕过——这是安全漏洞，不是笔误。

## 7. 资产化指标（怎么证明它在变好）

| 指标 | 定义 | 目标 |
|---|---|---|
| `success_rate` | 该 playbook 在 bench 正样本上的 VERIFIED 率 | ≥0.85 |
| `steps_median` | 完成该 playbook 的步数中位数 | 越少越好 |
| `abort_rate` | `applies_when` 满足但 abort 的比例 | <0.15（高了说明前置条件写松了） |
| `pitfall_hit_rate` | `pitfalls` 里描述的情况实际发生的比例 | 下降 = 经验有效 |
| **存活率** | 半年后仍在用的 playbook / 总数 | >0.7（低了说明当初就没想清） |

**最后一个是关键**：playbook 最容易变成"写完就没人看"。
存活率逼我们定期清理——**只增不减的资产库等于没有资产库**。

## 8. 与 `18`（oracle 目录）的分工

| 层 | 谁写 | 内容 | 变不变 |
|---|---|---|---|
| `18` oracle | 开发 | 判定逻辑、CWE、证据要求 | 极少变（语义变更才升版本） |
| `31` playbook | **渗透工程师** | 打法顺序、前置、坑 | 常变（经验积累） |

**这个分工是刻意的**：写代码的人写不出"1..50 枚举会触发风控"这种知识。
所以 playbook 的 owner 必须是做过渗透的人。
**如果让开发写 playbook，它会写成一堆 `run_oracle` 的堆砌——那就没有价值了。**

## 9. 本期交付的最小集（3 个，不是 14 个）

不要求 14 类 oracle 都有 playbook。本期只做**最能体现"打法差异"的三个**：

| # | playbook | 为什么选它 |
|---|---|---|
| 1 | `pb-idor-dual-account` | 最经典的"双账号 + 对照"，坑最多（N-03 教训） |
| 2 | `pb-ssrf-oob-first` | OOB 通道要先自测再打，顺序错了全是 INCONCLUSIVE |
| 3 | `pb-jwt-alg-none` | 必须先证明"服务器验签"再测绕过，否则全是误报 |

**三个的共同点**：它们都有"**顺序错了就全废**"的特性。
这正是 playbook 存在的意义——**oracle 不会告诉你顺序，playbook 会。**

## 10. 验收（B9 收尾）

1. 3 个 playbook 通过 `schemas/playbook.schema.json` + 三条语义检查。
2. 每个 playbook 在 bench 上跑出 `success_rate`（非 null）。
3. `pitfalls` 至少 2 条，且每条 `guard` 指向具体步骤（不许写空话）。
4. `make lint-noroute` 通过（playbook 里 0 地址）。
5. 故意让 playbook 调一个 `reversible_write` 的 oracle 但标 `read_only` → **启动失败**（验第 6 §3 条）。
6. 把某个 playbook 的 `applies_when` 改成永不满足 → 报表里进"未覆盖"，**不进 FN**。
