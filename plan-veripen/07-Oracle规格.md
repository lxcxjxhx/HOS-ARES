# 07 · Oracle 通用规格（契约 + 基类详解）

> 判据只有一个：**凡是"两个请求能对比"的，都能变成确定性判定；只有需要读语义的才交给模型。**
> 本期 **14 类** oracle 全部满足这条（完整目录见 `18-Oracle目录（14类判定矩阵）.md`），
> 所以全部离线可测、可 100% 复现。本文 §0 给出**所有 14 类共用的判定骨架**与冻结的函数签名，
> §1–§4 详述 4 个基类的实现细节，其余 10 类按同一骨架 + 同一契约扩展。
>
> **v3 改写原则**：oracle 描述的**永远是对参数施加的变换**，不是地址。
> 下面所有示例里的"变体"都写成 `参数变换描述符`；地址由 `RouteBinding` 提供（`27`）。

## 0. 通用规格（4 个都遵守）

| 项 | 规格 |
|---|---|
| 接口 | 见 `01-包结构与内核契约.md` §3.1（v3：入参 `RouteBinding`，出参 `RouteCall`） |
| 判定 | `judge()` 纯函数；无 IO / 无时钟 / 无随机 |
| 证据 | 每个变体都存 baseline+probe 两条 trace，对照另存 control |
| 至少一个对照 | **没有对照就不许出 VERIFIED**（防"整站都 200"式误报） |
| 重复 | 每个变体 `repeat=2`，两次结论一致才算成立（抗抖动） |
| 参数 | 全部来自 profile `oracles.params.<id>`，带 schema |
| **选择面** | 通过 `selector_tags` 向 `routes` 要候选；**不写路径、不写参数名绑定** |
| **绑定强度** | 判定结果带 `route_strength`；弱绑定不出高严重度（`06 §7`） |
| 版本 | 语义变更 → `version` 升位；capsule 记录 |
| 负样本 | 每个 oracle 必须有 ≥2 个"应判 REFUTED"的用例（见 11） |

**三条件合取 = 出洞**：`probe 表现符合漏洞` ∧ `baseline 表现正常` ∧ `control 表现符合预期`。任一不成立 → 不得 VERIFIED。

### 0.1 变体描述符（v3 的类型，替代 v2 的"变体字符串"）

```python
@dataclass(frozen=True)
class Transform:
    """对绑定的参数做一次可复现的变换。不含地址、不含参数名绑定。"""
    kind: Literal["append", "replace", "wrap_quote", "wrap_numeric_bool",
                  "prefix_relative", "encode", "swap_role", "offset_id"]
    target: Literal["param_value", "path_param", "body_field", "header"]
    arg: str                      # 例 "AND 1=1" / "1" / "//<OOB_REF>" / "0x2e2e"
    note: str                     # 人读说明，进 capsule 的 assertion

@dataclass(frozen=True)
class Variant:
    name: str                     # "bool_true" / "bool_false" / "control" / "alg_none" ...
    role: Literal["baseline", "probe", "control", "readback"]
    transform: Transform
    expect: Literal["vuln", "normal", "deny", "no_callback"]
```

**为什么不用字符串拼 payload**：字符串拼法必然要拼到"哪个参数、什么位置"，
一旦参数名也在代码里，就退化成硬路由。`Transform` 把"施加什么"与"施加到哪"分开：
`target` 是结构位置，具体参数名由 `RouteBinding.param_schema` 提供。

### 0.2 十四条共用的判定骨架（写 oracle 时照这个填，不要自创流程）

所有 14 类都能装进同一个骨架。差别只在"变异什么"和"比什么"，不在"怎么走流程"。

```python
def run(oracle, ctx, binding) -> Judgement:
    # ① 取基线（必须先有基线，且基线要"正常"）
    base = send(binding, params=oracle.baseline_params(binding))
    if not oracle.baseline_ok(base):
        return BLOCKED("baseline_abnormal")       # ← 环境问题，不是漏洞

    # ② 生成变体（含 control），数量受 max_variants 约束
    variants = oracle.plan(ctx, binding)          # list[Variant]，必含 ≥1 control
    if not any(v.role == "control" for v in variants):
        return INCONCLUSIVE("no_control")          # ← 缺对照一律不出 VERIFIED

    # ③ 逐个发（repeat=2），收集 trace
    traces = []
    for v in variants:
        for r in range(oracle.params.repeat):
            traces.append((v, send(binding, params=apply(v.transform, binding), role=v.role)))

    # ④ 纯函数判定
    pairs = pair_up(base, traces)                  # 每个变体与基线成对
    j = oracle.judge(pairs)                        # 无 IO / 时钟 / 随机

    # ⑤ 三条件合取校验（框架强制，不靠 oracle 自觉）
    if j.verdict == VERIFIED and not three_way_holds(pairs):
        return INCONCLUSIVE("three_way_failed")    # ← 防御性：oracle 写错了也不会出洞

    return j
```

**五处必须由框架做、不许 oracle 自己做**：

| # | 由框架做 | 若交给 oracle 会怎样 |
|---|---|---|
| ① | 基线正常性检查 | 每个 oracle 各写一套，标准不一 |
| ② | "必须含 control" 检查 | 有人图省事跳过 control → FP |
| ③ | `repeat` 循环 | 有人用 `repeat=1` 提速度 → 抖动进结论 |
| ④ | `judge` 纯函数隔离 | 有人在 judge 里读时钟 → 破坏可复现 |
| ⑤ | **`three_way_holds` 兜底** | oracle 写错时**仍然出 VERIFIED** → 直接毁掉 FP=0 |

第 ⑤ 条是刻意的冗余：`three_way_holds(pairs)` 是框架级的独立实现，
**不复用 oracle 的 judge 逻辑**。两条独立路径都成立才出 VERIFIED。
这是 FP=0 的第二道锁——第一道是 oracle 自己写对，第二道是框架不信它。

```python
def three_way_holds(pairs) -> bool:
    """框架级独立校验：不看 oracle 的判词，只看 trace 本身。"""
    probe    = [p for p in pairs if p.variant_role == "probe"]
    baseline = [p for p in pairs if p.variant_role == "baseline"]
    control  = [p for p in pairs if p.variant_role == "control"]
    if not (probe and baseline and control):
        return False                                   # 缺一不成
    return (all(p.probe_differs(p.baseline) for p in probe)
            and all(p.control_matches_expectation() for p in control)
            and all(p.baseline_is_normal() for p in baseline))
```

### 0.2.1 骨架依赖的 8 个函数，逐个定义

骨架里出现了 8 个名字。**它们的签名和语义必须冻结**，否则 14 个 oracle 会各自实现一套。

| 函数 | 谁实现 | 签名 | 语义（一句话） |
|---|---|---|---|
| `baseline_params(binding)` | **oracle** | `-> dict[str,int\|str]` | 从 `binding.param_schema` 取"契约声明的合法样例值"；**不猜、不造** |
| `baseline_ok(trace)` | **oracle** | `-> bool` | 基线是否"正常"（2xx 且不含错误指纹）。默认实现：`200 ≤ status < 300` |
| `plan(ctx, binding)` | **oracle** | `-> list[Variant]` | 产出变体列表，**必须含 ≥1 个 `role="control"`** |
| `apply(transform, binding)` | **框架** | `-> dict[str,str]` | 把 `Transform` 施加到 binding 的参数上，求值 `{{}}` 占位符（§0.4） |
| `send(binding, params, role)` | **框架** | `-> HttpTrace` | 经 `tools.http` 发出：四处 scope 校验 + 令牌桶 + 4KB 截断 |
| `pair_up(base, traces)` | **框架** | `-> list[RequestPair]` | 每个 `variant` 与其 `repeat` 次 trace 组成对，附基线 |
| `judge(pairs)` | **oracle** | `-> Judgement` | **纯函数**；无 IO/时钟/随机；不看 `route` 信息 |
| `three_way_holds(pairs)` | **框架** | `-> bool` | 独立复算三条件（下面 §0.2.2 给完整语义） |

**分工铁律**：`oracle` 只实现 4 个（`baseline_params` / `baseline_ok` / `plan` / `judge`），
其余 4 个由框架提供。oracle **不得**自己发请求、不得自己求值模板、不得自己配对。

**为什么 `baseline_params` 要 oracle 实现而不是框架**：只有 oracle 知道自己的语义
（idor 需要"属于 primary 的对象 id"，sqli 需要"一个正常可查的值"）。
框架给不出这个语义，硬要给就会变成"猜参数值"——又一个猜。

### 0.2.2 `three_way_holds` 的完整语义（这是 FP=0 的第二把锁）

```python
def three_way_holds(pairs: list[RequestPair]) -> bool:
    """独立实现：只读 trace 的 status / len / 规范化 token 集 / body 哈希。
       刻意不看 oracle 的 assertion，也不看它的 confidence。"""
    by_role = group_by_role(pairs)
    if not (by_role.probe and by_role.baseline and by_role.control):
        return False

    # 条件 A：基线正常（且两次重复一致）
    if not all(t.is_normal() for t in by_role.baseline):
        return False
    if not stable(by_role.baseline):            # repeat 之间指纹一致
        return False

    # 条件 B：probe 与基线有差异（且重复之间一致）
    for p in by_role.probe:
        if not differs(p.trace, p.baseline_trace):   # status / len_bucket / token 集
            return False
        if not stable(p.repeats):
            return False

    # 条件 C：control 符合预期（且重复之间一致）
    for c in by_role.control:
        if not matches(c.trace, c.expect):          # expect ∈ {deny, no_callback, normal}
            return False
        if not stable(c.repeats):
            return False

    return True
```

| 子判定 | 判据 | 阈值（全局，不许 per-oracle 覆盖） |
|---|---|---|
| `is_normal()` | `200 ≤ status < 300` 且不含错误指纹（`sqli_error_based` 用它的指纹库） | — |
| `stable(repeats)` | 所有 repeat 的 `(status, len_bucket, sorted_token_set_hash)` 完全相同 | 严格相等 |
| `differs(a, b)` | `status` 不同 **或** `|Δlen| ≥ 8` **或** token 集差异 ≥1 | 三者任一 |
| `matches(trace, expect)` | `deny` → status ∈ {401,403}；`no_callback` → 无回调记录；`normal` → `is_normal()` | — |

#### 0.2.4 可选第 4 条件：`hardened_absent`（借用 `29 零件 A`）

`three_way_holds` 的三条件能排除"看起来像洞"，但排不掉**"这个洞在补丁后依然存在"**这类误判——
因为我们的三条件都在**同一环境**里。靶场若提供 hardened 实例，可启用第 4 条件：

| 条件 | 判据 | 何时启用 |
|---|---|---|
| `hardened_absent` | 同一 `RouteCall` 在 hardened 资产上**不再产生 probe/control 差分** | 仅当 `profile.scope.assets` 里声明了 `role: hardened` 的孪生资产 |

| 情形 | 判定 |
|---|---|
| 三条件成立 ∧ hardened 差分消失 | `VERIFIED`（并且 `assertion.hardened_absent = true`） |
| 三条件成立 ∧ hardened **仍有**差分 | `INCONCLUSIVE("hardened_still_vulnerable")` → **优先怀疑打错了面**，不是"洞更严重" |
| 未声明 hardened 资产 | 跳过该条件（`assertion.hardened_absent = null`），行为与本条未引入时一致 |

**边界（必须写进代码注释）**：第 4 条件**只能加强、不能绕过**前三条件。
实现上它是 `three_way_holds` 里的 `and`，不是 `or`——写错一个词，FP=0 就没了。

**为什么值得借**：它把"补丁验证"从"人工比对两次报告"变成"判定的一部分"。
代价是靶场要多一份 hardened 实例（`25` 增可选能力），收益是"改完还复发"这类误判直接消失。

**三个刻意的"全局阈值"**（不允许 oracle 自己调）：

| 阈值 | 值 | 为什么全局 |
|---|---|---|
| `len_bucket` | `len // 8` | 让"差 3 字节"不算差分、让"差 12 字节"算；per-oracle 调会退化成"调到能过为止" |
| `stable` 严格相等 | — | 放宽成"相似"就等于允许抖动进结论 |
| `differs` 三选一 | — | 加第四种（如"DOM 结构"）会让不同 oracle 的判据不可比 |

**这套阈值与 `bench` 的关系**：`10 §3` 的 FP 门槛 0 是**结果要求**；
`three_way_holds` 是**过程保证**。前者靠后者才可能达成——不是靠事后抽查。

### 0.2.3 骨架的返回码映射（谁负责给 BLOCKED / INCONCLUSIVE）

| 情况 | 返回 | 谁判 | 说明 |
|---|---|---|---|
| 基线异常 | `BLOCKED(baseline_abnormal)` | 框架 | 环境问题，**不产出 finding** |
| 无 control | `INCONCLUSIVE(no_control)` | 框架 | 前置检查在 `plan` 后立刻做 |
| repeat 之间不一致 | `INCONCLUSIVE(unstable)` | 框架 | 抖动，不是漏洞 |
| `three_way_holds` 失败但 judge 说 VERIFIED | `INCONCLUSIVE(three_way_failed)` | 框架 | **最危险的一类：oracle 写错了** |
| 绑定不可用 | `BLOCKED(route_too_weak / route_unresolved)` | guard | 不发请求（`27 §3.9`） |
| 其余 | 由 `judge` 决定 | oracle | 但 VERIFIED 还需过 `three_way_holds` |

**注意最后一行**：`judge` 可以给 `INCONCLUSIVE` 或 `REFUTED`，**但不能单方面给 `VERIFIED`**。
VERIFIED 是 **oracle ∧ 框架** 的合取结果。这是 `01 §3.1` 那张表的实现层含义。


### 0.3 `Transform` 的精确语义（8 种，不许新增）

| kind | 语义 | 用于 |
|---|---|---|
| `append` | 在参数值末尾追加 `arg` | 布尔注入、报错注入 |
| `replace` | 整体替换为 `arg` | SSRF/跳转/文件名的目标值 |
| `wrap_quote` | 用 `arg` 包裹参数值（`arg` 是引号种类） | 引号闭合 |
| `wrap_numeric_bool` | 值 + `arg`（如 `1 AND 1=1`） | 布尔差分 |
| `prefix_relative` | 在前面加协议相对前缀 | 开放跳转的 `//` 变体 |
| `encode` | 对 `arg` 指定的字符集做编码（`arg` 为编码名） | 遍历的编码变体 |
| `swap_role` | 换认证角色（`arg` 为 role id） | 越权 |
| `offset_id` | 对标识参数加偏移（`arg` 为整数） | 越权对照资源 |

**新增第 9 种 kind 的条件**：现有 8 种**无法表达**你需要的变换，且该变换在 2 个以上 oracle 里复用。
只为单个 oracle 服务的特殊变换，应该用 `append`/`replace` 表达，不要新增 kind。

**为什么限制 kind 数量**：每种 kind 都要在 `three_way_holds` 里有对应的比较语义。
kind 越多，框架越难保证"独立校验"的完备性——最后只能退化成"信任 oracle"。

### 0.4 载荷模板与 nonce 插值（替代"再发明几个 kind"）

很多变体需要**动态内容**（nonce、随机串、深度计算值）。若为它们各加一个 kind，
`Transform` 会膨胀到不可维护。解法：`arg` 是**模板字符串**，支持 4 个占位符。

| 占位符 | 求值时机 | 值来源 |
|---|---|---|
| `{{nonce}}` | 每次发送前 | 本次任务唯一随机串（`secrets.token_hex(8)`），**判定只看"是否出现预期 nonce"** |
| `{{depth}}` | `plan()` 时一次 | 由 `binding.template` 的路径层数算出（`path_traversal` 用） |
| `{{role}}` | `plan()` 时一次 | 变体声明的 role id（`swap_role` 用） |
| `{{ref:NAME}}` | 每次发送前 | 从 `config/bindings/sources.yaml` 的符号解析（OOB 接入点等） |

**常见变体怎么用现有 8 种 kind 表达（不许发明新的）**

| 需求 | 写法 | 说明 |
|---|---|---|
| 目录穿越 | `append(arg="../" × {{depth}}) ` | `{{depth}}` 由框架算；**不在 oracle 里写死 `../../../../`** |
| XSS · HTML 体 | `replace(arg="<svg onload=alert('{{nonce}}')>")` | 三上下文只是**模板不同**，kind 都是 `replace` |
| XSS · 属性内 | `replace(arg="\" onmouseover=\"alert('{{nonce}}')\"")` | 同上 |
| XSS · JS 字符串 | `replace(arg="';alert('{{nonce}}')//")` | 同上 |
| SSRF | `replace(arg="{{ref:OOB}}/{{nonce}}")` | OOB 接入点是符号，不是常量 |
| 开放跳转 | `replace(arg="{{ref:OOB}}/{{nonce}}")` / `prefix_relative(arg="{{ref:OOB}}/{{nonce}}")` | 两种变体用两种 kind |
| JWT `alg=none` | `replace(arg="none")`（target=header） | 改 header 字段值 |

> **关键收益**：XSS 的三个上下文不再是三个 kind，而是三份模板。
> 这样新增一类 XSS（如 `<script>` 变体）只改 `cases.json` 的模板，不动代码，也不动 kind 集合。

**`{{nonce}}` 的三条纪律**

1. **判定不看 nonce 值本身**，只看"预期 nonce 是否出现在该出现的位置"（`11 §5`）。
2. nonce 必须**每次发送重新生成**吗？不。同一变体的 `repeat` 两次用**同一个 nonce**——
   否则无法比较"两次是否一致"。
3. nonce 必须进 capsule（`assertion.observed` 里），否则 replay 无法验证。
   但它**不是凭据**，不脱敏。

### 0.5 正负样本的构造规则（`cases.json`，每个 oracle 必交）

| 项 | 要求 |
|---|---|
| 正样本 | ≥4 条，且**必须覆盖三种难度**：直接触发 / 需参数变换 / 需对照成立 |
| 负样本 | ≥2 条，且**必须包含以下两类之一**：①"看似漏洞实则安全"（对照失败）②"环境异常"（应 INCONCLUSIVE 而非 VERIFIED） |
| 格式 | 每条 = `{variant_list, expected_verdict, why, tags}`，**不含地址**；模板用 §0.4 的占位符 |
| 来源 | 正负样本必须来自**真实靶场录制**（`make record`），不许手写响应体（`26`） |
| 冻结 | 进 Git；跑完删用例 = 作弊（`10 §9`） |

**"必须覆盖三种难度"的理由**：只测"直接触发"会漏掉"需要引号闭合"的真实场景（P-02 就是这种）。
而只测难题会让简单回归发现不了。三档齐备才叫覆盖。

**负样本的第二类常被漏掉**：大多数负样本只写"安全实现"，
不写"环境异常"。但 INCONCLUSIVE 路径如果不测，它就会在真实环境里第一次被走到——那时是生产。

---

## 1. `sqli_bool_diff` · SQL 注入布尔差分

| 项 | 内容 |
|---|---|
| CWE | CWE-89 |
| impact | read_only |
| selector_tags | `["param_injectable"]`（带可枚举参数的调用面） |
| requires | — |
| 适用 | 绑定里存在**标量参数**（数字/字符串），且基线响应可稳定取指纹 |
| 变体构造 | ① `bool_true`：`wrap_numeric_bool(AND 1=1)` 或 `wrap_quote(AND '1'='1')`<br>② `bool_false`：`wrap_numeric_bool(AND 1=2)` 或 `wrap_quote(AND '1'='2')`<br>③ `control_true/false`：同长度同形态的**无害**变换（`replace 1→1x` / `1→1y`） |
| 判定 | `VERIFIED` iff：<br>· `fingerprint(true) != fingerprint(false)`<br>· `fingerprint(control_true) == fingerprint(control_false)`<br>· 上述在 2 次重复中均成立 |
| 差分指纹 | 先规范化（去时间戳/uuid/csrf token/随机 nonce）再取 `(status, len_bucket, sorted_token_set_hash)`，**禁止裸字符串比较** |
| 证据 | 4 条 trace + `assertion.fingerprint` 四元组 |
| 误报控制 | ① 必须对照 ② 长度差分阈值 `|Δlen| >= 8` 或 token 集差异 >=1 ③ 排除"参数本身非法导致 500"（若 control 也 500 则 INCONCLUSIVE） |
| 已知限制 | 时间盲注不做（不稳定，归二期）；写 `INCONCLUSIVE` 而非猜测 |

> 这一类的精髓是**对照**：只做差分，任何"参数一变页面就变"的站点都会假阳性。
> 注意：`arg` 里的 `AND 1=1` 是**载荷**，不是路径——载荷由本 oracle 拥有，路径由绑定提供。

---

## 2. `idor_cross_account` · 越权（对象级）

| 项 | 内容 |
|---|---|
| CWE | CWE-639 |
| impact | read_only |
| selector_tags | `["object_read"]` |
| requires | `auth:secondary`（缺则 BLOCKED） |
| 适用 | `RouteSet` 中存在 `object_read` 标签的绑定，且该绑定有**对象标识参数**（`param_shape.kind ∈ {int, uuid, token}`） |
| 变体构造 | ① `baseline`：primary 读资源 R（期望 200，且 R 归属 primary）<br>② `probe`：`swap_role(secondary)` 读同一 R（期望被拒）<br>③ `control`：`swap_role(secondary)` + `offset_id` 读一条**明确不属于自己**的资源（期望 403） |
| 判定 | `VERIFIED` iff：<br>· `probe.status ∈ {200,204}` 且 `probe.body_sha256 == baseline.body_sha256`<br>· `control.status ∈ {401,403}`<br>· （说明：不是"整站不鉴权"，而是"鉴权了但漏了对象级"） |
| 关键设计 | `control` 是灵魂：没有它，any 未登录即可访问的公开接口都会被误报成 IDOR |
| 证据 | 3 条 trace（**两条不同 `route_ref`**：R 与 control 资源）+ 两个账号的角色标注（不存凭据） |
| 误报控制 | ① 排除同一账号的 R（先证明 R 属于 primary）② 要求 body 完全相同（不只是 200）③ `id_range` 内取 3~5 个样本，至少 2 个成立 |
| 报告措辞 | "对象级授权缺失（BOLA）"，并注明"仅读操作验证，未尝试修改" |

> **v3 变化**：v2 里 `resource_paths` 由 profile 写死，现在由 `object_read` 选择器解析。
> 若 `RouteSet` 中有 0 条 `object_read` 绑定 → `BLOCKED(route_unresolved)`，**不猜路径**。

---

## 3. `ssrf_oob` · 服务端请求伪造（外带证明）

| 项 | 内容 |
|---|---|
| CWE | CWE-918 |
| impact | read_only |
| selector_tags | `["url_fetch"]` |
| requires | 本地 OOB 通道（docker 内网 listener，**不依赖公网**） |
| 适用 | 绑定里存在 `kind: url` 的参数（形状由 `param_shape` 给出，不由 oracle 猜参数名） |
| 变体构造 | ① `probe`：参数值 = `OOB_REF/<nonce>`（`OOB_REF` 来自 bindings，nonce 为本次唯一串）<br>② `baseline`：参数值 = 契约声明的合法样例值（期望正常业务响应）<br>③ `control`：参数值 = `INVALID_REF/<nonce2>`（期望不产生回调） |
| 判定 | `VERIFIED` iff：<br>· listener 在 `callback_timeout_s` 内收到含 `nonce` 的请求<br>· `nonce2` **从未**出现（排除"任何 URL 都被请求"的误判）<br>· 回调源 IP ≠ 本 runner 的 IP（排除自己打的）<br>· 回调头/UA 与服务端特征一致（尽力而为，不作为唯一依据） |
| 证据 | 请求 trace + **回调记录**（源 IP、时间、路径哈希、UA）+ nonce |
| 误报控制 | ① nonce 唯一且不可猜 ② control 必须无回调 ③ 排除"目标把 URL 原样回显给客户端"（若响应体出现 nonce 但 listener 无回调 → INCONCLUSIVE） |
| 离线兼容 | listener 是本地容器，**接入点由 `config/bindings/sources.yaml` 的符号提供**；`make demo` 全程无外网 |

> 这是唯一"需要环境配合"的 oracle，所以额外给一条规则：**listener 不在线时一律 INCONCLUSIVE，不许猜**。
> 另一条 v3 规则：**OOB 接入点不是硬编码常量**。它的符号在配置里，实际值由部署注入——
> 否则"OOB 域名"就成了系统里最容易被复制粘贴的那个字面量。

---

## 4. `jwt_replay` · JWT 校验缺陷（复现型）

| 项 | 内容 |
|---|---|
| CWE | CWE-347 / CWE-798 |
| impact | read_only |
| selector_tags | `["token_issue", "protected_read"]` |
| requires | 至少一个可用 JWT（从登录响应或 profile 给定 `secret_ref`） |
| 变体构造 | ① `baseline`：原 token 访问受保护绑定（期望 200）<br>② `alg_none`：header 改 `{"alg":"none"}` + 去签名（期望被拒）<br>③ `claim_tamper`：改 `sub`/`role` 为特权值，**保留原签名**（期望被拒）<br>④ `control`：原 token 改一位无关字符/无效签名（期望被拒，证明服务器确实校验签名） |
| 判定 | `VERIFIED` iff：<br>· `baseline=200` ∧ `control ∈ {401,403}`（服务器确实校验）<br>· 且（`alg_none=200` ∨ `claim_tamper` 返回特权数据） |
| 关键设计 | `control` 证明"服务器会校验签名"，否则"所有乱 token 都 200"会被误判成漏洞 |
| 证据 | 4 条 trace + token 的 header/payload（**签名不存原文，只存前缀+长度**） |
| 误报控制 | ① 必须 baseline 成功 ② 必须 control 失败 ③ tamper 后返回的资源必须**可证明属于特权用户**（含特权标记字段），否则 INCONCLUSIVE |
| 报告措辞 | 三类分开写：`alg=none 可接受` / `签名未校验` / `声明可篡改` —— 混在一起是外行报告 |

> **token 获取也是路由问题**：`token_issue` 绑定由 discovery 提供（`25` 靶场会发布该语义标签）。
> 代码里不出现任何"登录接口"的猜测。

---

## 5. 十四条的能力边界（诚实清单，写进报告附录）

| 不做 | 原因 | 归属 |
|---|---|---|
| 时间盲注 | 抖动大，稳定性差，误报高 | 二期（需多次统计+置信区间） |
| 存储型 XSS | 需要多步+多角色+渲染验证 | 二期 |
| 反序列化 | 需要具体语言栈指纹与 gadget | 三期 |
| 业务逻辑（改价/竞态） | 需要语义理解与业务建模 | 三期（车企报告最关注 → 二期优先） |
| 内网横向 | 越界风险，非本期 | 不做 |
| 上传/包含 | 需要实际落盘与包含链 | 三期 |
| **路径爆破找洞** | 属于"发现面"，不属于"验证漏洞"；只在 `probe` 源里以受控、需审批的方式存在 | 不做（`05 §8`） |

> 面试价值：**能清楚说出"我们不做什么、为什么不"** 比"我们什么都做"可信得多。
> 最后一条是 v3 新增，也是本章与 `27` 的衔接点：**探索与验证分离**，探索有独立门控。

## 6. 新增一个 oracle 的清单（半天到一天）

```
1  veripen_oracles/<id>/judge.py     纯函数；**只判断，不做流程**（流程由 §0.2 骨架提供）
2  oracle.py                         applicable / plan（返回 Variant 列表）/ params_schema /
                                     selector_tags / baseline_params / baseline_ok
3  cases.json                        ≥4 正（三档难度）+ ≥2 负（含环境异常类），只含 Transform
4  meta.yaml                         id/version/cwe/severity 规则/描述/帮助/标签
5  params 进 profile schema          默认值 + 上线值 + selector 引用（不写地址）
6  报告映射                          cwe、severity 规则、措辞模板
7  测试                              tests/unit 跑 judge（吃 cases.json）
                                     tests/oracle 打靶场（真发请求）
                                     tests/routes 确认 selector 能解析到绑定
8  bench                            用例加进固定集（不许 cherry-pick）
```

**三步验收（缺一不算完成）**

| 验收 | 命令 | 期望 |
|---|---|---|
| 判定正确 | `pytest tests/unit -k <id>` | 正全 VERIFIED、负全 REFUTED/INCONCLUSIVE |
| 不碰内核 | `git diff --stat veripen_core/` | 空 |
| 零地址 | `make lint-noroute` | 0 命中 |

**外加一条针对 oracle 的专项检查**：

```python
def test_judge_does_not_read_environment(oracle):
    """judge 里出现这些符号即失败：说明判定被环境污染。"""
    banned = {"time", "datetime", "random", "os.environ", "open", "socket", "requests"}
    assert not (judge_imports(oracle) & banned)
```

**为什么在 §6 才给这条**：它是 `test_judge_is_pure` 的静态版本。
动态版本（跑 100 次比结果）能抓"结果不稳"，但抓不到"读了环境但恰好每次都一样"。
两个版本都要有——这就是"判据纯函数"在实现层的双重保证。


