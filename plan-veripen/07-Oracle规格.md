# 07 · Oracle 通用规格（契约 + 基类详解）

> 判据只有一个：**凡是"两个请求能对比"的，都能变成确定性判定；只有需要读语义的才交给模型。**
> 本期 **14 类** oracle 全部满足这条（完整目录见 `18-Oracle目录（14类判定矩阵）.md`），
> 所以全部离线可测、可 100% 复现。本文详述其中 4 个基类的实现细节，其余 10 类按同一契约扩展。

## 0. 通用规格（4 个都遵守）

| 项 | 规格 |
|---|---|
| 接口 | 见 `01-包结构与内核契约.md` §3.1 |
| 判定 | `judge()` 纯函数；无 IO / 无时钟 / 无随机 |
| 证据 | 每个变体都存 baseline+probe 两条 trace，对照另存 control |
| 至少一个对照 | **没有对照就不许出 VERIFIED**（防"整站都 200"式误报） |
| 重复 | 每个变体 `repeat=2`，两次结论一致才算成立（抗抖动） |
| 参数 | 全部来自 profile `oracles.params.<id>`，带 schema |
| 版本 | 语义变更 → `version` 升位；capsule 记录 |
| 负样本 | 每个 oracle 必须有 ≥2 个"应判 REFUTED"的用例（见 11） |

**三条件合取 = 出洞**：`probe 表现符合漏洞` ∧ `baseline 表现正常` ∧ `control 表现符合预期`。任一不成立 → 不得 VERIFIED。

---

## 1. `sqli_bool_diff` · SQL 注入布尔差分

| 项 | 内容 |
|---|---|
| CWE | CWE-89 |
| impact | read_only |
| requires | — |
| 适用 | URL 带可枚举数字/字符串参数的请求（`?id=1`） |
| 变体构造 | ① `bool_true`：`id=1 AND 1=1` 及等价的 `1' AND '1'='1`<br>② `bool_false`：`id=1 AND 1=2` 及 `1' AND '1'='2`<br>③ `control_true/control_false`：同长度同形态的**无害**参数对（如 `id=1x` / `id=1y`） |
| 判定 | `VERIFIED` iff：<br>· `fingerprint(true) != fingerprint(false)`<br>· `fingerprint(control_true) == fingerprint(control_false)`<br>· 上述在 2 次重复中均成立 |
| 差分指纹 | 先规范化（去时间戳/uuid/csrf token/随机 nonce）再取 `(status, len_bucket, sorted_token_set_hash)`，**禁止裸字符串比较** |
| 证据 | 4 条 trace + `assertion.fingerprint` 四元组 |
| 误报控制 | ① 必须对照 ② 长度差分阈值 `|Δlen| >= 8` 或 token 集差异 >=1 ③ 排除"参数本身非法导致 500"（若 control 也 500 则 INCONCLUSIVE） |
| 已知限制 | 时间盲注不做（不稳定，归二期）；写 `INCONCLUSIVE` 而非猜测 |

> 这一类的精髓是**对照**：只做差分，任何"参数一变页面就变"的站点都会假阳性。

---

## 2. `idor_cross_account` · 越权（对象级）

| 项 | 内容 |
|---|---|
| CWE | CWE-639 |
| impact | read_only |
| requires | `auth:secondary`（缺则 BLOCKED） |
| 适用 | profile 给出 `resource_paths` 与 `id_range` |
| 变体构造 | ① `baseline`：primary 读资源 R（期望 200，拿到"归属 primary"的标记）<br>② `probe`：secondary 读同一 R（期望被拒）<br>③ `control`：secondary 读一个**明确不属于自己**的其它资源 C（期望 403） |
| 判定 | `VERIFIED` iff：<br>· `probe.status ∈ {200,204}` 且 `probe.body_sha256 == baseline.body_sha256`<br>· `control.status ∈ {401,403}`<br>· （说明：不是"整站不鉴权"，而是"鉴权了但漏了对象级"） |
| 关键设计 | `control` 是灵魂：没有它，any 未登录即可访问的公开接口都会被误报成 IDOR |
| 证据 | 3 条 trace + 两个账号的角色标注（不存凭据） |
| 误报控制 | ① 排除同一账号的 R（先证明 R 属于 primary）② 要求 body 完全相同（不只是 200）③ `id_range` 内取 3~5 个样本，至少 2 个成立 |
| 报告措辞 | "对象级授权缺失（BOLA）"，并注明"仅读操作验证，未尝试修改" |

---

## 3. `ssrf_oob` · 服务端请求伪造（外带证明）

| 项 | 内容 |
|---|---|
| CWE | CWE-918 |
| impact | read_only |
| requires | 本地 OOB 通道（docker 内网 listener，**不依赖公网**） |
| 适用 | 参数疑似被服务端取用为 URL（`url=`、`webhook=`、`image=`、`callback=`） |
| 变体构造 | ① `probe`：参数值 = `http://oob.internal/<nonce>`，nonce 为本次唯一随机串<br>② `baseline`：参数值 = 合法同域 URL（期望正常业务响应）<br>③ `control`：参数值 = `http://invalid.invalid/<nonce2>`（期望不产生回调） |
| 判定 | `VERIFIED` iff：<br>· listener 在 `callback_timeout_s` 内收到含 `nonce` 的请求<br>· `nonce2` **从未**出现（排除"任何 URL 都被请求"的误判）<br>· 回调源 IP ≠ 本 runner 的 IP（排除自己打的）<br>· 回调头/UA 与服务端特征一致（尽力而为，不作为唯一依据） |
| 证据 | 请求 trace + **回调记录**（源 IP、时间、路径、UA）+ nonce |
| 误报控制 | ① nonce 唯一且不可猜 ② control 必须无回调 ③ 排除"目标把 URL 原样回显给客户端"（若响应体出现 nonce 但 listener 无回调 → INCONCLUSIVE） |
| 离线兼容 | listener 是本地容器，域名走 compose 内 DNS；`make demo` 全程无外网 |

> 这是唯一"需要环境配合"的 oracle，所以额外给一条规则：**listener 不在线时一律 INCONCLUSIVE，不许猜**。

---

## 4. `jwt_replay` · JWT 校验缺陷（复现型）

| 项 | 内容 |
|---|---|
| CWE | CWE-347 / CWE-798 |
| impact | read_only |
| requires | 至少一个可用 JWT（从登录响应或 profile 给定） |
| 变体构造 | ① `baseline`：原 token 访问受保护接口（期望 200）<br>② `alg_none`：header 改 `{"alg":"none"}` + 去签名（期望被拒）<br>③ `claim_tamper`：改 `sub`/`role` 为特权值，**保留原签名**（期望被拒）<br>④ `control`：原 token 加一位无关空白/无效签名（期望被拒，证明服务器确实校验签名） |
| 判定 | `VERIFIED` iff：<br>· `baseline=200` ∧ `control ∈ {401,403}`（服务器确实校验）<br>· 且（`alg_none=200` ∨ `claim_tamper` 返回特权数据） |
| 关键设计 | `control` 证明"服务器会校验签名"，否则"所有乱 token 都 200"会被误判成漏洞 |
| 证据 | 4 条 trace + token 的 header/payload（**签名不存原文，只存前缀+长度**） |
| 误报控制 | ① 必须 baseline 成功 ② 必须 control 失败 ③ tamper 后返回的资源必须**可证明属于特权用户**（含特权标记字段），否则 INCONCLUSIVE |
| 报告措辞 | 三类分开写：`alg=none 可接受` / `签名未校验` / `声明可篡改` —— 混在一起是外行报告 |

---

## 5. 四个 oracle 的能力边界（诚实清单，写进报告附录）

| 不做 | 原因 | 归属 |
|---|---|---|
| 时间盲注 | 抖动大，稳定性差，误报高 | 二期（需多次统计+置信区间） |
| 存储型 XSS | 需要多步+多角色+渲染验证 | 二期 |
| 反序列化 | 需要具体语言栈指纹与 gadget | 三期 |
| 业务逻辑（改价/竞态） | 需要语义理解与业务建模 | 三期（车企报告最关注 → 二期优先） |
| 内网横向 | 越界风险，非本期 | 不做 |
| 上传/包含 | 需要实际落盘与包含链 | 三期 |

> 面试价值：**能清楚说出"我们不做什么、为什么不"** 比"我们什么都做"可信得多。

## 6. 新增一个 oracle 的清单（半天到一天）

```
1  veripen_oracles/<id>/judge.py     纯函数 + 三条件合取
2  cases.json  ≥4 正样本 + ≥2 负样本（judge 单测直接吃）
3  oracle.py  applicable/plan/params_schema
4  params 进 profile schema（默认值 + 上线值）
5  报告映射：cwe、severity 规则、措辞模板
6  tests/oracle 打靶场跑通；tests/unit 跑 judge
7  bench 用例加进固定集（不许 cherry-pick）
```

**验收**：`git diff --stat veripen_core/` 为空。
