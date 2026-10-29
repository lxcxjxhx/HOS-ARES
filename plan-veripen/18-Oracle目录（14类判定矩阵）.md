# 18 · Oracle 目录（14 类判定矩阵）

> `07-Oracle规格.md` 定义**通用契约与 4 个基类**；本文是**完整目录**：14 个验证器，分三档。
> 分档依据不是难度，是**证据的确定性强度**——A 档两请求即可判定，C 档需要渲染/协议配合。

## 0. 总表

| 档 | oracle_id | CWE | 关键对照 | 波次 |
|---|---|---|---|---|
| A | `sqli_bool_diff` | CWE-89 | control 参数对 | W1 |
| A | `idor_cross_account` | CWE-639 | 双账号 + 无权资源 403 | W1 |
| A | `ssrf_oob` | CWE-918 | nonce 回调 + control 无回调 | W1 |
| A | `jwt_replay` | CWE-347 | 原 token 200 ∧ 无效签名 401 | W1 |
| A | `open_redirect` | CWE-601 | 合法跳转 vs 外域跳转 | W2 |
| A | `path_traversal` | CWE-22 | 越界路径 vs 正常路径内容指纹 | W2 |
| B | `sqli_error_based` | CWE-89 | 报错指纹 vs 正常响应 | W2 |
| B | `bola_functional` | CWE-862 | 低权访问管理接口 vs 高权对照 | W2 |
| B | `xss_reflected_ctx` | CWE-79 | 三种上下文各自的转义证据 | W2 |
| B | `mass_assignment` | CWE-915 | 注入只读字段后回读是否被采纳 | W2 |
| B | `rate_limit_absent` | CWE-770 | 并发 vs 串行的成功数差 | W3 |
| C | `xss_stored_probe` | CWE-79 | 二次读取（不同角色/会话）命中 nonce | W4 |
| C | `cors_misconfig` | CWE-942 | 恶意 Origin 的 ACAO 回显 + 凭据 | W4 |
| C | `graphql_introspection` | CWE-200 | introspection 可用 + 字段级鉴权缺失 | W4 |

**A 档 6 个是"证据强度最高"的一批**：全部满足"两个请求能对比"，判定不受环境影响。
B 档需要更多步骤或多角色；C 档需要渲染或协议层配合，误差最大，报告里必须标注证据强度。

---

## 1. A 档（6）

### 1.1 `sqli_bool_diff`（详见 07 §1）
不变。补充：`compare_mode` 增加 `structural`（去随机 token 后比较 DOM 结构哈希），用于 SPA 场景。

### 1.2 `idor_cross_account`（详见 07 §2）
不变。补充 `resource_paths` 支持从 OpenAPI 自动派生（见 19）。

### 1.3 `ssrf_oob`（详见 07 §3）
不变。补充：OOB 通道支持 `dns`（仅记录解析请求）与 `http`（记录完整请求）两种，DNS 模式用于目标不回显任何东西时。

### 1.4 `jwt_replay`（详见 07 §4）
扩展变体：`kid_injection`（kid 指向本地文件/路径穿越）、`jku_ssrf`（jku 指向 OOB）。判定同构：baseline 200 ∧ control 401 ∧ 变体 200。

### 1.5 `open_redirect`（CWE-601）

| 项 | 规格 |
|---|---|
| impact | read_only |
| requires | — |
| 变体 | ① baseline：`?next=/dashboard` → 302 到站内<br>② probe：`?next=https://oob.internal/<nonce>` → 302 到外域<br>③ probe2：`?next=//oob.internal/<nonce>`（协议相对）<br>④ control：`?next=javascript:alert(1)` → 应被拒（证明有过滤但漏了协议相对） |
| 判定 | VERIFIED iff：probe 或 probe2 的 `Location` 头解析后 host == oob 域，且 baseline 的 Location 为站内，且 control 被拒 |
| 误报控制 | ① 只看 `Location` 头，不看 body 里的链接 ② 允许"跳到白名单外域"由 profile 声明（`open_redirect.allowed_hosts`），命中白名单即 REFUTED ③ 排除"所有参数都被回显到页面"的站 |
| 证据 | 4 条 trace 的 `Location` 头（不跟跳，`max_redirects=0`） |

### 1.6 `path_traversal`（CWE-22）

| 项 | 规格 |
|---|---|
| impact | read_only |
| requires | — |
| 变体 | ① baseline：`?file=report.txt`（存在文件）→ 200 + 内容指纹<br>② probe：`?file=../../../../etc/passwd`（或 Windows `..\..\win.ini`）<br>③ control：`?file=../../../../nonexistent_<nonce>.txt` → 应 404/403 |
| 判定 | VERIFIED iff：probe 200 且 body 命中**目标系统文件特征指纹**（`root:x:0:0` / `[extensions]`），且 control 非 200 |
| 误报控制 | ① 必须有系统文件特征指纹，不能只凭 200 ② control 必须失败（排除"任何 file 参数都返回首页"）③ 编码变体（`%2e%2e%2f`、`..%5c`）只在基础变体失败后尝试，最多 3 个 |
| 证据 | 3 条 trace + 指纹命中的字节区间（脱敏后） |
| 报告措辞 | 区分"任意文件读取"与"目录穿越但受限于白名单" |

---

## 2. B 档（5）

### 2.1 `sqli_error_based`（CWE-89）

| 项 | 规格 |
|---|---|
| 与 1.1 的区别 | 布尔差分看"真/假页面差异"；报错注入看"错误指纹是否被触发" |
| 变体 | probe：`id=1'` / `id=1"` / `id=1)` 三种闭合<br>control：`id=1<nonce>`（非法但不会触发 SQL 解析） |
| 判定 | VERIFIED iff：probe 响应出现**数据库错误指纹**（`SQL syntax`、`ORA-`、`pg_query`、`SQLSTATE`、`Unclosed quotation`），且 control 无该指纹，且 baseline 无该指纹 |
| 误报控制 | ① 指纹库版本化（`fingerprints.yaml`，可扩展）② 必须 baseline/control 双无 ③ 排除"错误页含全站通用报错模板"（同一指纹在 control 也出现则排除） |
| 证据 | 3 条 trace + 命中的指纹名 + 响应片段 |

### 2.2 `bola_functional`（CWE-862）功能级越权

| 项 | 规格 |
|---|---|
| requires | `auth:secondary`（低权）+ `auth:primary`（高权或普通） |
| 变体 | ① baseline：高权账号访问 `/admin/*` 管理接口 → 200<br>② probe：低权账号访问同一接口 → 期望 403<br>③ control：低权账号访问自己应有的接口 → 200（证明账号可用） |
| 判定 | VERIFIED iff：baseline 200 ∧ probe 200 ∧ control 200 |
| 与 IDOR 的区别 | IDOR 是"同接口不同对象"，BOLA-functional 是"同对象不同功能"；报告分开写 |
| 误报控制 | ① control 必过（否则是账号本身失效）② 管理接口清单来自 profile `bola_functional.admin_paths` 或 OpenAPI 派生 ③ 只看状态码+是否返回管理数据标记 |

### 2.3 `xss_reflected_ctx`（CWE-79）

| 项 | 规格 |
|---|---|
| 变体 | 对同一参数用 3 个上下文探针：HTML 体 `<vp{n}>`、属性内 `" onx=vp{n}`、JS 字符串 `';vp{n}//` |
| 判定 | VERIFIED iff：探针字符串以**未转义原样**出现在响应中（字节级匹配），且该上下文对应的闭合字符也未被转义 |
| 误报控制 | ① 必须是**未转义**出现，出现 `<vp123>` 被转成 `&lt;vp123&gt;` 即 REFUTED ② 不执行 JS、不启动浏览器（那是 C 档）③ 每上下文独立判定，报告写清"哪个上下文、哪个闭合字符" |
| 证据 | 3 条 trace + 命中位置的字节偏移与前后 64 字节 |

### 2.4 `mass_assignment`（CWE-915）

| 项 | 规格 |
|---|---|
| 变体 | ① baseline：`PATCH /users/me {name:"x"}` → 200，回读 `role` 不变<br>② probe：`PATCH /users/me {name:"x", role:"admin"}` → 200<br>③ 回读：`GET /users/me` 看 `role` 是否变 admin |
| 判定 | VERIFIED iff：probe 200 ∧ 回读 `role == admin` ∧ baseline 回读 `role != admin` |
| 误报控制 | ① 必须回读验证（不能只看 PATCH 200，很多框架会忽略未知字段）② 只注入 profile 声明的"只读字段"清单 ③ 恢复：本类会改状态 → `impact: reversible_write`，**必须过审批门**，且回滚到原值（记入 capsule） |
| 备注 | 这是 14 类里唯一写操作。默认在 profile 里 `enabled: false`，客户授权才开 |

### 2.5 `rate_limit_absent`（CWE-770）

| 项 | 规格 |
|---|---|
| 变体 | ① 串行：同一接口 10 次，间隔 1s → 记成功数 S1<br>② 并发：10 并发 → 记成功数 S2，429/403 数 R2 |
| 判定 | VERIFIED iff：S2 == 10 且 R2 == 0 且 S1 == 10（即完全没有限流），且接口是"应当限流"的类型（登录/发码/查询，由 profile 声明） |
| 误报控制 | ① 只测 profile 白名单内的接口 ② 全局 QPS 上限必须低于测试速率（否则自己就是限流）③ 结果标 `confidence: 0.7`（这是"配置缺失"而非"漏洞"，报告分级为 info/low） |
| 风险 | 本类会打目标 10 并发 → 默认关闭，且必须 `policy.max_impact_class` 允许 |

---

## 3. C 档（3，需渲染或协议配合）

### 3.1 `xss_stored_probe`（CWE-79）

| 项 | 规格 |
|---|---|
| 变体 | ① 写入：A 账号提交含 `<vp{nonce}>` 的内容<br>② 读取：B 账号（不同会话）打开展示页<br>③ 判定：B 拿到的 HTML 中 nonce 未转义出现 |
| 判定 | VERIFIED iff：二次读取的响应里 nonce 原样出现且未被实体化 |
| 误报控制 | ① 必须**跨会话**读取（同会话可能是本地回显）② 必须证明写入成功（先 GET 确认内容存在）③ 清理：写入的记录打 `nonce` 标记，任务结束尝试删除（`reversible_write`） |
| 归属 | W4；依赖浏览器可选（本期用 HTTP 直读 HTML，不用无头浏览器，降低不稳定） |

### 3.2 `cors_misconfig`（CWE-942）

| 项 | 规格 |
|---|---|
| 变体 | ① baseline：`Origin: https://same.internal` → 期望 ACAO 精确匹配或不回<br>② probe：`Origin: https://oob.internal` → 看 `Access-Control-Allow-Origin` 是否回显该值<br>③ probe2：同上 + `Access-Control-Allow-Credentials: true` |
| 判定 | VERIFIED iff：probe 回显 `*` 或回显 `oob` 域；若同时 `allow-credentials: true` 则升级为 high |
| 误报控制 | ① 必须用 OPTIONS 预检与 GET 各测一次 ② `*` 且无 credentials → 仅 low ③ 排除 `Vary: Origin` 未设置但实际不回显的情况（以响应头为准） |

### 3.3 `graphql_introspection`（CWE-200）

| 项 | 规格 |
|---|---|
| 变体 | ① introspection query → 是否返回 schema<br>② 对返回的 mutation 列表，用低权账号尝试一个"应受限"的 mutation |
| 判定 | VERIFIED iff：introspection 可用 ∧ 至少一个受限 mutation 在低权下成功 |
| 误报控制 | ① 仅 introspection 可用 = info（很多产品故意开放）② 必须叠加鉴权缺失才算漏洞 ③ 需要 GraphQL 端点（profile 声明或 OpenAPI 派生） |

---

## 4. 判定矩阵（一张表看完 14 类）

| oracle | 需要账号 | 需要 OOB | 会改状态 | 证据强度 | 默认启用 |
|---|---|---|---|---|---|
| sqli_bool_diff | — | — | 否 | 高 | ✅ |
| idor_cross_account | 双 | — | 否 | 高 | ✅ |
| ssrf_oob | — | ✅ | 否 | 高 | ✅ |
| jwt_replay | 单 | — | 否 | 高 | ✅ |
| open_redirect | — | ✅ | 否 | 高 | ✅ |
| path_traversal | — | — | 否 | 高 | ✅ |
| sqli_error_based | — | — | 否 | 中高 | ✅ |
| bola_functional | 双 | — | 否 | 中高 | ✅ |
| xss_reflected_ctx | — | — | 否 | 中 | ✅ |
| mass_assignment | 单 | — | **是** | 中 | ❌ 需授权 |
| rate_limit_absent | — | — | 否（但压测） | 中 | ❌ 需授权 |
| xss_stored_probe | 双 | — | **是** | 中 | ❌ 需授权 |
| cors_misconfig | — | — | 否 | 中 | ✅ |
| graphql_introspection | 单 | — | 否 | 低 | ❌ 有 GraphQL 才开 |

**默认启用 9 类，需授权 4 类，条件启用 1 类。** 这个默认值本身是"敢用"的证据。

## 5. 插件清单（新增 oracle 的机械步骤，见 07 §6）

```
veripen_oracles/<id>/
├─ meta.yaml        id/version/cwe/severity规则/描述/帮助/标签
├─ oracle.py        applicable/plan/params_schema/impact/requires
├─ judge.py         纯函数
├─ fingerprints.yaml（仅错误类/内容类需要）
└─ cases.json       ≥4 正 + ≥2 负
```

**新增一类 ≈ 半天**（agent 辅助下 1–2 小时），因为判定是纯函数、请求由框架发。
