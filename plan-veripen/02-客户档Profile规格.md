# 02 · 客户档 Profile 规格（v1）

> 这是"能卖第二家"的唯一实现机制。字段一旦定好，客户差异就只能在数据里表达。
> `apiVersion: veripen.profile/v1` · 校验：pydantic + JSON Schema 双份（后者给客户填）

## 1. 设计原则

1. **只描述"许可"和"约束"，不描述"怎么做"**——怎么打是 oracle 的事。
2. **默认拒绝**：任何布尔型安全开关的默认值都是"不许可"。
3. **可哈希**：profile 全量规范化后算 `profile_hash`，进每条 capsule。改动 profile = 证据失效，必须重跑。
4. **单文件单客户**：`profiles/<name>/profile.yaml`，不允许 include 别的客户档。
5. **密钥不入档**：只出现 `secret_ref`，值在本地 vault（文件+文件锁，见 05）。

## 2. 完整字段表

```yaml
apiVersion: veripen.profile/v1

meta:
  name: demo-a                  # 目录名一致
  customer_id: DEMO-A-001       # 客户标识（只允许出现这里）
  industry: saas                # saas | auto | finance | gov | energy
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
  assets:
    - id: web-01
      kind: url                 # domain|ip|cidr|url|repo
      value: http://dvwa.local
      criticality: medium       # low|medium|high|critical
      tags: [web, php]
  deny:                         # 显式拒绝（优先级最高，覆盖 assets）
    - 169.254.169.254/32        # 云元数据
    - 10.0.0.0/8                # 内网段（本期不打内网）
  dns_policy: strict            # strict=解析结果必须落在 assets 的解析集合内
  max_redirects: 0              # 本期禁止跟随跳转（防 SSRF 借道）

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
      resource_paths: ["/api/users/{id}/profile", "/api/orders/{id}"]
      id_range: [1, 50]
    ssrf_oob:
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

> 一律**启动即失败**，不做"跑起来再发现"。`make doctor` 就是这套校验的 CLI 化身。

## 4. 两个示例档（这就是 A1 断言的物料）

| | `demo-a`（通用企业 SaaS） | `demo-b`（车云 API 形态） |
|---|---|---|
| assets | `http://dvwa.local`、`http://juice.local` | `http://tsp-lab.local`（自带 OpenAPI，真实数据） |
| auth | basic 双账号 | bearer + 一个短寿命 JWT + mTLS 客户端证书（lab 用自签） |
| oracles | 9 类默认开 + 4 类需授权 | `idor_cross_account` + `jwt_replay` + `ssrf_oob` + `bola_functional` + `graphql_introspection` |
| params | 默认 | idor 路径来自 OpenAPI 的 `/vehicles/{vin}` 等 |
| policy | 20 QPS / 2 并发 | 10 QPS / 1 并发（车云更保守） |
| report | owasp_wstg + cwe | 加 `iso21434`、`un_r155` 章节 |
| 用途 | 证明通用企业接入 | 证明**换行业只换 YAML** |

**A1 断言的实质**：这两个档的差异全部在上表右侧列，`core/` 一行没改。

## 5. profile_hash 与证据失效

```
profile_hash = sha256( canonical_json( profile 去掉 meta.owner 与 auth.*.secret_ref) )
```

- 进每条 capsule 的 `env.profile_hash`。
- 报告头部打印 `profile_hash` 前 12 位。
- 客户改了 scope 或参数 → hash 变 → 旧 capsule 标记 `stale`，`replay` 时提示"profile 已变更，证据需重跑"。

## 6. 新增一个客户档的 SOP（成本 = 半天，不是一周）

```
1  cp -r profiles/demo-a profiles/<new>
2  改 meta / scope.assets / auth.providers
3  oracles.enabled 只开该客户合同允许的类别
4  policy 按客户运维窗口调 QPS 与并发
5  report.frameworks 按合同映射
6  make doctor PROFILE=<new>          # 启动即失败机制在这里暴露问题
7  make demo PROFILE=<new>            # 跑通即交付
```

**验收**：`git diff --stat veripen_core/ veripen_oracles/` 为空。
