# 18 · Oracle 目录（14 类判定矩阵）

> `07-Oracle规格.md` 定义**通用契约、判定骨架与 4 个基类**；本文是**完整目录**：14 个验证器，分三档。
> 分档依据不是难度，是**证据的确定性强度**——A 档两请求即可判定，C 档需要渲染/协议配合。
>
> **v3 改写**：本目录**不出现任何路径、参数名绑定、端点**。
> 每个 oracle 声明两件事：要什么 `selector_tags`（向 `route_discovery` 要面），
> 以及施加什么 `Transform`（对参数做的变换）。地址与参数名都在绑定里（`27`）。

## 0. 总表

| 档 | oracle_id | CWE | selector_tags | 关键对照 | 波次 |
|---|---|---|---|---|---|
| A | `sqli_bool_diff` | CWE-89 | `param_injectable` | control 参数对 | W1 |
| A | `idor_cross_account` | CWE-639 | `object_read` | 双账号 + 无权资源 403 | W1 |
| A | `ssrf_oob` | CWE-918 | `url_fetch` | nonce 回调 + control 无回调 | W1 |
| A | `jwt_replay` | CWE-347 | `token_issue` + `protected_read` | 原 token 200 ∧ 无效签名 401 | W1 |
| A | `open_redirect` | CWE-601 | `redirect_entry` | 合法跳转 vs 外域跳转 | W2 |
| A | `path_traversal` | CWE-22 | `file_read` | 越界路径 vs 正常路径内容指纹 | W2 |
| B | `sqli_error_based` | CWE-89 | `param_injectable` | 报错指纹 vs 正常响应 | W2 |
| B | `bola_functional` | CWE-862 | `admin_function` + `normal_function` | 低权访问管理面 vs 高权对照 | W2 |
| B | `xss_reflected_ctx` | CWE-79 | `echo_param` | 三种上下文各自的转义证据 | W2 |
| B | `mass_assignment` | CWE-915 | `profile_write` | 注入只读字段后回读是否被采纳 | W2 |
| B | `rate_limit_absent` | CWE-770 | `rate_sensitive` | 并发 vs 串行的成功数差 | W3 |
| C | `xss_stored_probe` | CWE-79 | `content_write` + `content_read` | 二次读取（不同角色/会话）命中 nonce | W4 |
| C | `cors_misconfig` | CWE-942 | `api_read` | 恶意 Origin 的 ACAO 回显 + 凭据 | W4 |
| C | `graphql_introspection` | CWE-200 | `graphql_endpoint` | introspection 可用 + 字段级鉴权缺失 | W4 |

**A 档 6 个是"证据强度最高"的一批**：全部满足"两个请求能对比"，判定不受环境影响。
B 档需要更多步骤或多角色；C 档需要渲染或协议层配合，误差最大，报告里必须标注证据强度。

**selector_tags 的含义**（`27 §3.1`）：标签由**契约/观测**给出，不由我们定义端点。
靶场与客户系统在它们的机器可读契约里标注这些语义；没有标签的系统，`route_discovery` 会返回空集，
对应 oracle 直接 `BLOCKED(route_unresolved)` —— **这不是失败，是诚实的"打不了"**。

---

## 1. A 档（6）

### 1.1 `sqli_bool_diff`（详见 07 §1）
不变。补充：`compare_mode` 增加 `structural`（去随机 token 后比较 DOM 结构哈希），用于 SPA 场景。
`selector_tags` 额外接受 `param_injectable@body`，表示变换施加在 body 字段而非 query 参数。

### 1.2 `idor_cross_account`（详见 07 §2）
不变。补充：`object_read` 的候选**由契约派生**（`19`），不来自 profile 的手写列表。
一条资产若有多个 `object_read` 绑定，按 `confidence → 参数类型为整型优先` 排序，取前者干。

### 1.3 `ssrf_oob`（详见 07 §3）
不变。补充：OOB 通道支持 `dns`（仅记录解析请求）与 `http`（记录完整请求）两种，
DNS 模式用于目标不回显任何东西时。**通道接入点是配置符号，不是常量。**

### 1.4 `jwt_replay`（详见 07 §4）
扩展变体：`kid_injection`（`kid` 指向本地文件的相对路径形态）、`jku_ssrf`（`jku` 指向 OOB）。
判定同构：baseline 200 ∧ control 401 ∧ 变体 200。
**`kid`/`jku` 的值是载荷，不是地址**：`jku` 用 `OOB_REF` 符号，`kid` 用相对路径载荷常量（这是攻击字符串，属于 oracle 的域知识）。

### 1.5 `open_redirect`

| 项 | 规格 |
|---|---|
| CWE | CWE-601 |
| impact | read_only |
| selector_tags | `["redirect_entry"]`（契约标注"接受跳转目标参数"的入口） |
| requires | — |
| 变体 | ① baseline：`replace(合法相对目标)` → 302 到站内<br>② probe：`replace(OOB_REF)` → 302 到外域<br>③ probe2：`prefix_relative(OOB_REF)`（协议相对形态）<br>④ control：`replace(javascript 伪协议)` → 应被拒（证明有过滤但漏了协议相对） |
| 判定 | VERIFIED iff：probe 或 probe2 的 `Location` 头解析后 host == OOB 域，且 baseline 的 Location 为站内，且 control 被拒 |
| 误报控制 | ① 只看 `Location` 头，不看 body 里的链接 ② 允许"跳到白名单外域"由 profile 声明（`open_redirect.allowed_hosts`），命中白名单即 REFUTED ③ 排除"所有参数都被回显到页面"的站 |
| 证据 | 4 条 trace 的 `Location` 头（不跟跳，`max_redirects=0`） |

### 1.6 `path_traversal`

| 项 | 规格 |
|---|---|
| CWE | CWE-22 |
| impact | read_only |
| selector_tags | `["file_read"]` |
| requires | — |
| 变体 | ① baseline：`replace(契约声明的合法文件名)` → 200 + 内容指纹<br>② probe：`append("../" × {{depth}} + 系统文件名)`（深度由 `{{depth}}` 算出，`07 §0.4`）<br>③ control：`append("../" × {{depth}} + "nonexistent_" + {{nonce}})` → 应 404/403 |
| 判定 | VERIFIED iff：probe 200 且 body 命中**目标系统文件特征指纹**（`root:x:0:0` / `[extensions]`），且 control 非 200 |
| 误报控制 | ① 必须有系统文件特征指纹，不能只凭 200 ② control 必须失败（排除"任何 file 参数都返回首页"）③ 编码变体（`%2e%2e%2f`、`..%5c`）只在基础变体失败后尝试，最多 3 个 |
| 证据 | 3 条 trace + 指纹命中的字节区间（脱敏后） |
| 报告措辞 | 区分"任意文件读取"与"目录穿越但受限于白名单" |
| **v3 说明** | 穿越深度不是常量：由 `RouteBinding.template` 的路径层数算出，作为 `append` 的 `arg` 传入（`07 §0.4`）。写死 `../../../../` 是硬路由的另一种形态 |

---

## 2. B 档（5）

### 2.1 `sqli_error_based`

| 项 | 规格 |
|---|---|
| CWE | CWE-89 |
| selector_tags | `["param_injectable"]` |
| 与 1.1 的区别 | 布尔差分看"真/假页面差异"；报错注入看"错误指纹是否被触发" |
| 变体 | probe：`wrap_quote(')` / `wrap_quote(")` / `wrap_quote())` 三种闭合<br>control：`replace(<nonce>)`（非法但不会触发 SQL 解析） |
| 判定 | VERIFIED iff：probe 响应出现**数据库错误指纹**（`SQL syntax`、`ORA-`、`pg_query`、`SQLSTATE`、`Unclosed quotation`），且 control 无该指纹，且 baseline 无该指纹 |
| 误报控制 | ① 指纹库版本化（`fingerprints.yaml`，可扩展）② 必须 baseline/control 双无 ③ 排除"错误页含全站通用报错模板"（同一指纹在 control 也出现则排除） |
| 证据 | 3 条 trace + 命中的指纹名 + 响应片段 |

### 2.2 `bola_functional`（功能级越权）

| 项 | 规格 |
|---|---|
| CWE | CWE-862 |
| selector_tags | `["admin_function", "normal_function"]`（两者都要，用于对照） |
| requires | `auth:secondary`（低权）+ `auth:primary`（高权或普通） |
| 变体 | ① baseline：高权账号打 `admin_function` 绑定 → 200<br>② probe：低权账号打**同一绑定** → 期望 403<br>③ control：低权账号打 `normal_function` 绑定 → 200（证明账号可用） |
| 判定 | VERIFIED iff：baseline 200 ∧ probe 200 ∧ control 200 |
| 与 IDOR 的区别 | IDOR 是"同接口不同对象"，BOLA-functional 是"同对象不同功能"；报告分开写 |
| 误报控制 | ① control 必过（否则是账号本身失效）② `admin_function` 清单由契约派生（不是 profile 手写）③ 只看状态码 + 是否返回管理数据标记 |
| **v3 说明** | v2 靠"路径里含 admin 关键词"判断（`19 §4` 打分项）。那是路径语义假设，现已改为**契约标签**。 |

### 2.3 `xss_reflected_ctx`

| 项 | 规格 |
|---|---|
| CWE | CWE-79 |
| selector_tags | `["echo_param"]` |
| 变体 | 对同一参数用 3 份**载荷模板**（kind 都是 `replace`，只是 `arg` 不同）：HTML 体 `<svg onload=alert('{{nonce}}')>`、属性内 `" onmouseover="alert('{{nonce}}')"`、JS 字符串 `';alert('{{nonce}}')//`（`07 §0.4`） |
| 判定 | VERIFIED iff：探针字符串以**未转义原样**出现在响应中（字节级匹配），且该上下文对应的闭合字符也未被转义 |
| 误报控制 | ① 必须是**未转义**出现，出现被实体化即 REFUTED ② 不执行 JS、不启动浏览器（那是 C 档）③ 每上下文独立判定，报告写清"哪个上下文、哪个闭合字符" |
| 证据 | 3 条 trace + 命中位置的字节偏移与前后 64 字节 |

### 2.4 `mass_assignment`

| 项 | 规格 |
|---|---|
| CWE | CWE-915 |
| selector_tags | `["profile_write", "profile_read"]`（写入 + 回读两条绑定） |
| 变体 | ① baseline：`replace(name,"x")` → 200，回读 `role` 不变<br>② probe：`append(role,"admin")` → 200<br>③ 回读：打 `profile_read` 绑定看 `role` 是否变 admin |
| 判定 | VERIFIED iff：probe 200 ∧ 回读 `role == admin` ∧ baseline 回读 `role != admin` |
| 误报控制 | ① 必须回读验证（不能只看写入 200，很多框架会忽略未知字段）② 只注入 profile 声明的"只读字段"清单 ③ 恢复：本类会改状态 → `impact: reversible_write`，**必须过审批门**，且回滚到原值（记入 capsule） |
| 备注 | 这是 14 类里唯一写操作。默认在 profile 里 `enabled: false`，客户授权才开 |

### 2.5 `rate_limit_absent`

| 项 | 规格 |
|---|---|
| CWE | CWE-770 |
| selector_tags | `["rate_sensitive"]` |
| 变体 | ① 串行：同一绑定 10 次，间隔 1s → 记成功数 S1<br>② 并发：10 并发 → 记成功数 S2，429/403 数 R2 |
| 判定 | VERIFIED iff：S2 == 10 且 R2 == 0 且 S1 == 10（即完全没有限流），且该绑定在契约里被标注为"应限流"类型 |
| 误报控制 | ① 只测 `rate_sensitive` 标签的绑定 ② 全局 QPS 上限必须低于测试速率（否则自己就是限流）③ 结果标 `confidence: 0.7`（这是"配置缺失"而非"漏洞"，报告分级为 info/low） |
| 风险 | 本类会打目标 10 并发 → 默认关闭，且必须 `policy.max_impact_class` 允许 |

---

## 3. C 档（3，需渲染或协议配合）

### 3.1 `xss_stored_probe`

| 项 | 规格 |
|---|---|
| CWE | CWE-79 |
| selector_tags | `["content_write", "content_read"]` |
| 变体 | ① 写入：A 账号对 `content_write` 打 `replace("<svg onload=alert('{{nonce}}')>")`<br>② 读取：B 账号（不同会话）打 `content_read`<br>③ 判定：B 拿到的 HTML 中该 nonce 未转义出现 |
| 判定 | VERIFIED iff：二次读取的响应里 nonce 原样出现且未被实体化 |
| 误报控制 | ① 必须**跨会话**读取（同会话可能是本地回显）② 必须证明写入成功（先读回确认内容存在）③ 清理：写入的记录打 `nonce` 标记，任务结束尝试删除（`reversible_write`） |
| 归属 | W4；依赖浏览器可选（本期用 HTTP 直读 HTML，不用无头浏览器，降低不稳定） |

### 3.2 `cors_misconfig`

| 项 | 规格 |
|---|---|
| CWE | CWE-942 |
| selector_tags | `["api_read"]` |
| 变体 | ① baseline：`Origin: SAME_ORIGIN_REF` → 期望 ACAO 精确匹配或不回<br>② probe：`Origin: OOB_REF` → 看 `Access-Control-Allow-Origin` 是否回显该值<br>③ probe2：同上 + `Access-Control-Allow-Credentials: true` |
| 判定 | VERIFIED iff：probe 回显 `*` 或回显 OOB 域；若同时 `allow-credentials: true` 则升级为 high |
| 误报控制 | ① 必须用 OPTIONS 预检与 GET 各测一次 ② `*` 且无 credentials → 仅 low ③ 排除 `Vary: Origin` 未设置但实际不回显的情况（以响应头为准） |

### 3.3 `graphql_introspection`

| 项 | 规格 |
|---|---|
| CWE | CWE-200 |
| selector_tags | `["graphql_endpoint"]` |
| 变体 | ① introspection query → 是否返回 schema<br>② 对返回的 mutation 列表，用低权账号尝试一个"应受限"的 mutation |
| 判定 | VERIFIED iff：introspection 可用 ∧ 至少一个受限 mutation 在低权下成功 |
| 误报控制 | ① 仅 introspection 可用 = info（很多产品故意开放）② 必须叠加鉴权缺失才算漏洞 ③ 需要 `graphql_endpoint` 标签的绑定 |
| **v3 说明** | 「哪个端点」不写死：标签来自契约。一个资产有多个 GraphQL 入口时，取 confidence 最高 |

---

## 4. 判定矩阵（一张表看完 14 类）

| oracle | 需要账号 | 需要 OOB | 会改状态 | 证据强度 | 默认启用 | 典型 selector |
|---|---|---|---|---|---|---|
| sqli_bool_diff | — | — | 否 | 高 | ✅ | `param_injectable` |
| idor_cross_account | 双 | — | 否 | 高 | ✅ | `object_read` |
| ssrf_oob | — | ✅ | 否 | 高 | ✅ | `url_fetch` |
| jwt_replay | 单 | — | 否 | 高 | ✅ | `token_issue` |
| open_redirect | — | ✅ | 否 | 高 | ✅ | `redirect_entry` |
| path_traversal | — | — | 否 | 高 | ✅ | `file_read` |
| sqli_error_based | — | — | 否 | 中高 | ✅ | `param_injectable` |
| bola_functional | 双 | — | 否 | 中高 | ✅ | `admin_function` |
| xss_reflected_ctx | — | — | 否 | 中 | ✅ | `echo_param` |
| mass_assignment | 单 | — | **是** | 中 | ❌ 需授权 | `profile_write` |
| rate_limit_absent | — | — | 否（但压测） | 中 | ❌ 需授权 | `rate_sensitive` |
| xss_stored_probe | 双 | — | **是** | 中 | ❌ 需授权 | `content_write` |
| cors_misconfig | — | — | 否 | 中 | ✅ | `api_read` |
| graphql_introspection | 单 | — | 否 | 低 | ❌ 有 GraphQL 才开 | `graphql_endpoint` |

**默认启用 9 类，需授权 4 类，条件启用 1 类。** 这个默认值本身是"敢用"的证据。

## 5. 插件清单（新增 oracle 的机械步骤，见 07 §6）

```
veripen_oracles/<id>/
├─ meta.yaml        id/version/cwe/severity规则/描述/帮助/标签/selector_tags
├─ oracle.py        applicable/plan/params_schema/impact/requires/selector_tags
├─ judge.py         纯函数
├─ fingerprints.yaml（仅错误类/内容类需要）
└─ cases.json       ≥4 正 + ≥2 负（Transform 与期望，无地址）
```

**新增一类 ≈ 半天**（agent 辅助下 1–2 小时），因为判定是纯函数、请求由框架发。

## 6. 标签字典（唯一权威，靶场与客户契约都按这个标注）

| 标签 | 语义 | 主要产出方式 |
|---|---|---|
| `object_read` | 读取单个业务对象（含对象标识参数） | 契约 `operationId`/`tags`；观测 |
| `object_list` | 列表读取（分页参数） | 契约；观测 |
| `admin_function` | 管理/内部功能面 | 契约安全声明；语义标注 |
| `normal_function` | 普通用户可用功能面（对照） | 契约安全声明 |
| `param_injectable` | 参数进入查询/模板/命令 | 契约参数类型；观测 |
| `url_fetch` | 服务端取用外部 URL | 契约参数 `format: uri` |
| `file_read` | 按名称读取文件/资源 | 契约参数名语义；响应 `content-disposition` |
| `redirect_entry` | 接受跳转目标 | 契约参数语义；3xx 观测 |
| `echo_param` | 参数回显到响应体 | 观测（回显检测） |
| `token_issue` | 签发令牌 | 契约安全声明；响应含 token 字段 |
| `protected_read` | 需令牌访问的读 | 契约安全声明 |
| `profile_write` | 当前用户资料写 | 契约 `PUT/PATCH` + 自指路径语义 |
| `profile_read` | 当前用户资料读 | 同上（对应的 GET） |
| `rate_sensitive` | 应限流的操作（登录/发码/查询） | 契约显式；或属 `token_issue` |
| `content_write` | 用户内容写入（自由文本） | 契约 body schema |
| `content_read` | 用户内容读取 | 契约；观测 |
| `api_read` | 常规 API 读（CORS 面） | 契约 `content-type: json` |
| `graphql_endpoint` | GraphQL 单一入口 | 契约 `content-type` 含 graphql |

**共 18 个标签。这份表与 `sync` 三处枚举必须完全一致**：

| 位置 | 形态 |
|---|---|
| 本文 §6 | 人读权威表 |
| `schemas/routeset.schema.json` → `semantic_tags.items.enum` | 绑定侧校验 |
| `schemas/profile.schema.json` → `routes.selectors[].semantic_tag.enum` | 客户档侧校验 |

一致性由 `check-plan.cjs` 机械检查（**少一个、多一个、拼错一个都会红**）。
这正是 v3 的原则在文档层的应用：语义约定也要能被机器验证，否则它会在某次编辑里悄悄漂移。

**新增标签的流程**：改本表 → 靶场契约同步标注 → `tests/routes` 加一条解析用例。
标签是**词汇表**，不是路由；它不含任何可定位信息，因此不受 `lint-noroute` 约束。
