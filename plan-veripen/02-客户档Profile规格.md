# 02 · 客户档 Profile 规格（v2）

> 这是"能卖第二家"的唯一实现机制。字段一旦定好，客户差异就只能在数据里表达。
> `apiVersion: veripen.profile/v2` · 校验：pydantic + JSON Schema 双份（后者给客户填）
>
> **v2 的破坏性变更**：删除 `oracles.params.*.resource_paths`。
> profile **不允许出现任何路径、URL、端口、endpoint**（见 `27-无硬路由架构.md` N3）。
> 客户要"指定打哪个接口"，表达方式是**语义选择器**（`route_select`），不是地址。

## 1. 设计原则

1. **只描述"许可"和"约束"，不描述"怎么做"，更不描述"在哪"**——地址归 `route_discovery`。
2. **默认拒绝**：任何布尔型安全开关的默认值都是"不许可"。
3. **可哈希**：profile 全量规范化后算 `profile_hash`，进每条 capsule。改动 profile = 证据失效，必须重跑。
4. **单文件单客户**：`profiles/<name>/profile.yaml`，不允许 include 别的客户档。
5. **密钥不入档**：只出现 `secret_ref`，值在本地 vault（文件+文件锁，见 05）。
6. **零地址**：profile 中出现的可网络定位字面量 = 启动即失败（`VP-1005`）。`assets[].value`
   是唯一例外，且它是**授权对象标识**，不进代码、不进提示词、不进 capsule 正文。

## 2. 完整字段表

```yaml
apiVersion: veripen.profile/v2

meta:
  name: demo-a                  # 目录名一致
  customer_id: DEMO-A-001       # 客户标识（只允许出现这里）
  industry: saas                # saas | auto | finance | gov | energy | other
  env: lab                      # lab | staging | prod  ← prod 本期不实现，字段先占位
  version: 1.0.0
  owner: secops@example.invalid
  data_class: internal          # public|internal|confidential|restricted

scope:
  engagement_id: ENG-2026-001   # 书面授权编号（合同/委托单号）
  window:                       # 测试窗口，超出窗口任何请求都拒绝
    start: "2026-11-01T09:00:00+08:00"
    end:   "2026-11-01T18:00:00+08:00"
  timezone: Asia/Shanghai
  assets:                       # ← 唯一允许出现可定位字面量的地方（授权对象标识）
    - id: web-01
      kind: url                 # domain|ip|cidr|url|repo
      value: ASSET_ENDPOINT_A   # 由客户在部署时填入，不进 git（.env 注入）
      criticality: medium       # low|medium|high|critical
      tags: [web, php]
  deny:                         # 显式拒绝（优先级最高，覆盖 assets）；网段用命名策略
    - DENY_SET_DEFAULT          # 展开为元数据/回环/私网/链路本地（见 05 §1.1）
  dns_policy: strict            # strict=解析结果必须落在 assets 的解析集合内
  max_redirects: 0              # 本期禁止跟随跳转（防 SSRF 借道）

# ── 新增：路由发现策略（替代 v1 的 resource_paths） ──────────────
routes:
  sources:                      # 允许使用的发现源，按优先级；空数组 = fail-closed
    - contract
    - observed
    - crawl
  contract_locator:             # 契约在哪本身也是 RouteQuery，不是地址
    enabled: true
    discovery_order: [observed, well_known, operator]
  probe_wordlists: []           # 词表 = 配置数据；空 = probe 源不参与（不报错、不猜）
  selectors:                    # 语义选择器：客户说"打哪一类"，不说"打哪个地址"
    - id: object_read           # 供 idor_cross_account 使用
      semantic_tag: object_read
      param_shape: { kind: int, name_hint: "*id*" }
      response_shape: { ct: json, has_field: "*id*" }
      auth_requirement: secondary
    - id: url_fetch             # 供 ssrf_oob 使用
      semantic_tag: url_fetch
      param_shape: { kind: url, name_hint: "*url*|*uri*|*target*|*webhook*|*callback*" }
      auth_requirement: none
  ttl_s: 900                    # 绑定有效期；过期即重发现（27 §3.9）
  max_rediscovery: 3            # 每任务上限（27 §4）
  min_confidence: 0.70          # 低于此值的绑定只用于排序，不发请求

auth:
  providers:
    - id: primary
      type: basic               # basic|bearer|jwt|oauth2|mtls|session_cookie
      username: alice
      secret_ref: vault://demo-a/primary   # 只有引用，没有明文
    - id: secondary             # idor_cross_account 必需
      type: basic
      username: bob
      secret_ref: vault://demo-a/secondary

oracles:
  enabled: [sqli_bool_diff, idor_cross_account, ssrf_oob, jwt_replay]
  params:
    sqli_bool_diff:
      max_variants: 4
      repeat: 2                 # 每个变体重复次数（稳定性）
      compare_mode: content_len+token   # 差分指纹策略
    idor_cross_account:
      selector: object_read     # 引用 routes.selectors.id，不再写路径
      id_range: [1, 50]
    ssrf_oob:
      selector: url_fetch
      callback_timeout_s: 10
      markers: [url_param, json_body]
    jwt_replay:
      tamper_claims: [sub, role]

policy:
  qps_global: 20
  burst_global: 40
  per_asset_concurrency: 2
  max_throttle_wait_s: 5
  approval_gates:
    - action_class: reversible_write
      when: always
      approver_role: secops_lead
    - action_class: scope_expand
      when: always
      approver_role: secops_lead
    - action_class: discovery_probe    # 新增：探测型发现（词表爆破）需门控
      when: always
      approver_role: secops_lead
  egress_allowlist: []          # 空 = 只允许 assets 解析集合
  data_egress: deny             # 日志/证据不出机（export 需审批+水印）
  max_impact_class: read_only
  stop_on_scope_violation: true # 一处越界，整任务停（不可配置为 false）

budget:
  max_steps: 120
  max_tokens: 400000
  max_wall_clock_s: 1800
  max_cost_usd: 5.0

report:
  frameworks: [owasp_wstg, cwe]     # 可选：iso21434 / un_r155 / un_r156 / gb_44495 / mlps
  lang: zh-CN
  watermark: true
  distribution: internal            # internal|partner|customer
  include_unverified: false         # 未验证发现是否单列（默认不列）
```

> `ASSET_ENDPOINT_A` / `DENY_SET_DEFAULT` 不是占位符风格问题，而是**部署期注入**：
> 客户在 `.env` 里给值，git 里只有符号。这样"客户地址"既不是代码也不是文档。**

## 3. 校验规则（schema 之外还要查的语义）

| 规则 | 失败行为 |
|---|---|
| `scope.assets` 为空 | **fail-closed 退出**，不建任务 |
| `window.end < now` 或 窗口 > 72h | 拒绝启动 |
| `deny` 与 `assets` 有交集 | 拒绝启动（不静默取交集） |
| `oracles.enabled` 含 `idor_cross_account` 但无 `secondary` 账号 | 拒绝启动（前置校验，不等到运行时） |
| `max_impact_class != read_only` 且无审批门 | 拒绝启动 |
| `param` 超出 `oracle.params_schema` | 拒绝启动，指出字段路径 |
| `secret_ref` 无法解析 | 拒绝启动（不降级为匿名） |
| 任何 `assets.value` 解析到私网/元数据地址且不在 `assets` 显式声明 | 拒绝启动 |
| **profile 内出现任何可网络定位字面量**（路径/URL/host/端口/`VERB /x`） | 拒绝启动 `VP-1005`，并指出行号 |
| **`routes.sources` 为空 且 `oracles.enabled` 全依赖 discovery** | 拒绝启动（会全程 `ROUTE_UNRESOLVED`，不如不跑） |
| **`routes.selectors[].id` 被 oracle 的 `selector` 引用但未定义** | 拒绝启动 |
| **`routes.min_confidence < 0.70`** | 拒绝启动（低于此值只允许排序，见 `27 §3.3`） |
| **`routes.ttl_s > window` 时长** | 拒绝启动（绑定会比授权窗口活得久） |

> 一律**启动即失败**，不做"跑起来再发现"。`make doctor` 就是这套校验的 CLI 化身。
> 最后一组四条是 v2 新增的"路由卫生"校验，`lint-noroute` 与 `doctor` 双重覆盖。

## 4. 两个示例档（这就是 A1 断言的物料）

| | `demo-a`（通用企业 SaaS） | `demo-b`（车云 API 形态） |
|---|---|---|
| assets 符号 | `ASSET_A1`、`ASSET_A2`（`.env` 注入） | `ASSET_B1`（自带机器可读契约 + 真实数据） |
| auth | basic 双账号 | bearer + 短寿命 JWT + mTLS 客户端证书（lab 用自签） |
| oracles | 9 类默认开 + 4 类需授权 | `idor_cross_account` + `jwt_replay` + `ssrf_oob` + `bola_functional` + `graphql_introspection` |
| routes.selectors | `object_read` / `url_fetch` / `file_read` | 加 `vehicle_read`、`admin_function`；`object_read` 的 `param_shape` 由契约派生 |
| policy | 20 QPS / 2 并发 | 10 QPS / 1 并发（车云更保守） |
| report | owasp_wstg + cwe | 加 `iso21434`、`un_r155` 章节 |
| 用途 | 证明通用企业接入 | 证明**换行业只换 YAML** |

**A1 断言的实质**：这两个档的差异全部在上表右侧列，`core/` 一行没改。
**A1 的前提是 A9**：两档都不能出现地址；地址全从 `assets` 符号 + `route_discovery` 来。
所以 `make demo` / `make demo-alt` 的前置动作都是 `make lint-noroute`。

## 5. profile_hash 与证据失效

```
profile_hash = sha256( canonical_json( profile 去掉 meta.owner 与 auth.*.secret_ref)
                       + canonical_json( resolved assets) )
```

- 进每条 capsule 的 `env.profile_hash`。
- 报告头部打印 `profile_hash` 前 12 位。
- 客户改了 scope 或参数 → hash 变 → 旧 capsule 标记 `stale`，`replay` 时提示"profile 已变更，证据需重跑"。
- **`assets` 的解析结果参与哈希**：同一个文件、换一个部署环境（不同接入点），证据必须失效。
  这是 v2 新增的一条——v1 只用文件内容，会跨环境误认"证据仍有效"。
- `routes` 段参与哈希：改了 `selectors` / `min_confidence` 就等于改了"打哪些面"，证据失效。

## 6. 新增一个客户档的 SOP（成本 = 半天，不是一周）

```
1  cp -r profiles/demo-a profiles/<new>
2  改 meta / scope.assets（符号）/ auth.providers
3  在 .env 里给 <new> 的 asset 符号填实际值（不进 git）
4  oracles.enabled 只开该客户合同允许的类别
5  routes.selectors 按客户业务语义写（不含地址）；probe_wordlists 留空除非已审批
6  policy 按客户运维窗口调 QPS 与并发
7  report.frameworks 按合同映射
8  make doctor PROFILE=<new>          # 启动即失败机制在这里暴露问题
9  make lint-noroute                  # 零地址
10 make demo PROFILE=<new>            # 跑通即交付
```

**验收**：`git diff --stat veripen_core/ veripen_oracles/` 为空，**且** `make lint-noroute` 通过。
