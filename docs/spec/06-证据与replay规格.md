# 06 · 证据模型与 replay 规格

> 这是整个项目**最值钱的一块**。产品与 demo 的分界线就在这里：
> demo 说"我发现了一个洞"，产品能说"这是证据，你现在自己复现一遍"。

## 1. 三条原则

1. **内容寻址**：capsule 的 id 就是自身内容的 sha256，同样输入必然得到同样字节。
2. **可脱离 agent**：`replay` 不加载模型、不做推理，只重发请求并重算判定。
3. **只增不改**：sealed 之后不可变；要修就新建，旧的留作审计。

## 2. capsule 完整 schema（`veripen.capsule/v1`）

```json
{
  "capsule_version": "1.0",
  "capsule_id": "sha256:9f2c...",
  "finding_id": "01JAB...",
  "created_at": "2026-11-01T02:14:07Z",

  "oracle": {
    "id": "idor_cross_account",
    "version": "1.0.0",
    "cwe": "CWE-639",
    "params_hash": "sha256:3a1b..."
  },

  "target": {
    "asset_id": "web-01",
    "url": "http://juice.local/api/users/2/profile",
    "method": "GET",
    "resolved_ip": "172.30.0.5",
    "userinfo_rejected": true
  },

  "env": {
    "profile_name": "demo-a",
    "profile_hash": "sha256:7ce4...",
    "image_digest": "sha256:dd11...",
    "runner_id": "worker-2@host-abc",
    "clock": { "t0": 1761966847, "t1": 1761966849, "timezone": "UTC" },
    "egress": { "mode": "whitelist", "proxy": null }
  },

  "steps": [
    {
      "n": 1,
      "role": "baseline",
      "account": "primary",
      "request": {
        "url": "http://juice.local/api/users/2/profile",
        "method": "GET",
        "headers": { "Cookie": "<redacted>", "Accept": "application/json" },
        "body": null,
        "body_sha256": null
      },
      "response": {
        "status": 200,
        "length": 412,
        "content_type": "application/json",
        "body_sha256": "sha256:aa12...",
        "body_excerpt_b64": "eyJpZCI6MiwidXNlcm5hbWUiOiJib2IifQ==",
        "excerpt_truncated": false
      },
      "peer_ip": "172.30.0.5",
      "elapsed_ms": 37,
      "err": null
    },
    {
      "n": 2,
      "role": "probe",
      "account": "secondary",
      "request": { "url": "http://juice.local/api/users/2/profile", "method": "GET",
                   "headers": { "Cookie": "<redacted>" }, "body": null },
      "response": { "status": 200, "length": 412,
                    "body_sha256": "sha256:aa12...",
                    "body_excerpt_b64": "eyJpZCI6MiwidXNlcm5hbWUiOiJib2IifQ==" },
      "peer_ip": "172.30.0.5",
      "elapsed_ms": 41,
      "err": null
    },
    {
      "n": 3,
      "role": "control",
      "account": "secondary",
      "note": "对照：访问自己无权拥有的资源应被拒绝",
      "request": { "url": "http://juice.local/api/users/3/profile", "method": "GET" },
      "response": { "status": 403, "length": 88, "body_sha256": "sha256:bb77..." },
      "peer_ip": "172.30.0.5",
      "elapsed_ms": 33
    }
  ],

  "assertion": {
    "kind": "cross_account",
    "statement": "secondary 账号读取 primary 拥有资源成功(200)，且对照资源被拒(403)",
    "expected": { "probe_status": [200, 204], "control_status": [401, 403] },
    "observed": { "probe_status": 200, "control_status": 403 },
    "fingerprint": { "probe_body_eq_baseline": true, "control_denied": true }
  },

  "verdict": "VERIFIED",
  "confidence": 0.95,
  "confidence_rule": "double_condition_satisfied",   // 分数来源可解释

  "redaction": {
    "policy": "pii-min",
    "fields": ["steps[*].request.headers.Cookie", "steps[*].request.headers.Authorization"],
    "secrets_in_vault": ["vault://demo-a/primary", "vault://demo-a/secondary"]
  },

  "chain": { "prev_sha256": "sha256:5c9a...", "self_sha256": "sha256:9f2c..." }
}
```

### 2.1 字段存在的理由（每条都说得出为什么）

| 字段 | 为什么必须有 |
|---|---|
| `env.profile_hash` / `image_digest` | 证据只在特定配置与镜像下成立，环境变了要标 stale |
| `env.clock` | 多源关联第一杀手是时钟；UTC 统一，另存 tz |
| `steps[*].role` | baseline/probe/control 三分类是"差分+对照"判定的输入 |
| `steps[*].peer_ip` | 证明打到的是范围内的 IP，不是 rebinding 后的黑洞 |
| `response.body_sha256` | 内容相同与否不靠人看，靠哈希 |
| `response.body_excerpt_b64` | 人读需要看一点，压缩后 4KB 上限 |
| `assertion.fingerprint` | 把"为什么判定成立"变成结构化数据，可回归 |
| `confidence_rule` | 置信度必须可解释，不许模型给分 |
| `redaction.secrets_in_vault` | 复现要凭据，但凭据不能进证据包 |
| `chain.prev/self` | 哈希链，防事后篡改某一条 |

## 3. 哈希链与防篡改

```
capsule_i.chain.prev_sha256 = capsule_{i-1}.chain.self_sha256
capsule_i.chain.self_sha256 = sha256( canonical_json(capsule_i 去掉 chain.self_sha256) )
```

- 规范化 JSON：键排序、无空白、浮点统一 6 位、时间统一 UTC RFC3339。
- `make verify-chain` 顺序校验整份报告的证据链，任何一条被改即断链并定位。
- 报告头部写 `root_sha256`（首条 capsule 的 prev），等价于"报告指纹"。
- 部署到客户侧时建议落 WORM 桶（对象锁）/ 只追加文件卷 —— 本期用文件 + 校验脚本模拟该语义。

## 4. 脱敏策略（`pii-min`）

| 对象 | 处理 |
|---|---|
| 请求头 `Cookie/Authorization/X-Api-Key` | 全替 `<redacted>`，值不落盘（vault 里按 ref 取） |
| 响应体 | 按正则脱敏：身份证、手机号、邮箱、车架号(VIN)、银行卡、JWT 字面量 |
| 响应体大小 | 保留前 4KB（分块截断标记 `excerpt_truncated`），完整正文落 `out/capsules/bodies/<sha>.bin`（本地、不进报告） |
| 用户自定义 | profile 可加 `redaction.extra_patterns[]` |
| 导出 | `--external` 时二次脱敏 + 水印 + `distribution` 标记 |

**可分享性验证**：把 capsule 发给第三方，他拿不到任何凭据，但仍能看懂"打了什么、回了什么、为什么判定成立"。

## 5. replay 语义（这是 A4 断言的全部内容）

```
veripen replay --capsule out/capsules/<id>.json [--profile demo-a] [--dry-run]
```

| 步骤 | 动作 | 失败输出 |
|---|---|---|
| 1 | 校验 `self_sha256` 与内容一致 | `CAPSULE_TAMPERED` |
| 2 | 校验 profile_hash / 当前 profile | `PROFILE_CHANGED`（不阻断，标 stale 后继续） |
| 3 | guard 三处校验（scope 现在是否仍允许） | `REPLAY_BLOCKED`（**这是安全行为，不是失败**） |
| 4 | 从 vault 取凭据，按原顺序重发 steps（含 control） | `NETWORK_ERROR`（可 `--retry 2`） |
| 5 | 重算 `assertion.fingerprint` 与 verdict | `ASSERTION_NOT_REPRODUCED` |
| 6 | 写 `capsules.replay_status` + `replay_at`；判定不成立时转 `ASSERTION_NOT_REPRODUCED` 并标 `needs_human` | — |

### 5.1 replay 结果的四种状态（
不要只说"成功/失败"）

| 状态 | 含义 | 报告里怎么写 |
|---|---|---|
| `REPLAYED_OK` | 判定完全复现 | "已于 <时间> 复现" |
| `REPLAYED_PARTIAL` | 关键响应变了但结论仍成立 | "结论成立，响应细节已变化" |
| `ASSERTION_NOT_REPRODUCED` | 判定不成立 | "疑似已修复或环境差异，需人工复核" |
| `REPLAY_BLOCKED` | 当前 scope/审批不允许 | "按当前授权不可复现"（不视为缺陷） |

第 4 种尤其重要：**它证明系统在"想复现但不该复现"时也会拦**，这是面试加分点。

## 6. replay 的三种运行模式

| 模式 | 用途 | 特点 |
|---|---|---|
| `--dry-run` | 演示/审计 | 不发包，只校验链路与哈希，打印将发的请求（脱敏后） |
| 默认（实时） | 客户现场 | 真发请求，需要凭据与网络 |
| `--offline-fixture` | 断网演示 | 用 capsule 内保存的响应重放判定函数，证明"判定是纯函数" |

第三种是离线演示的兜底：模型、网络、靶场全挂，`make replay-offline` 依然能跑，因为它只调纯函数。

## 7. 与报告的契约

| 规则 | 强制方式 |
|---|---|
| 报告每条发现必须带 `capsule_id` + `self_sha256` + `replay_status` | 渲染期断言，缺失直接抛异常（不降级、不跳过） |
| 报告头部写 `root_sha256` 与 `profile_hash` | 模板固定字段 |
| 未验证发现单独章节，且不得出现"确认/存在/高危"等措辞 | 措辞检查（`make lint-report`） |
| 同一 capsule 不得支撑两条不同结论的发现 | `ux_finding_run` + capsule 唯一引用 |

## 8. 验收（D2 收工标准）

1. 靶场 3 条发现 → 3 个 capsule 文件 → 哈希链完整。
2. 手工改一个字节 → `replay` 报 `CAPSULE_TAMPERED`（演示"防篡改"）。
3. `replay --dry-run` 与实时模式都通过；`--offline-fixture` 在断网容器内通过。
4. 把 scope 改小后 `replay` 返回 `REPLAY_BLOCKED`（演示"该拦的时候拦得住"）。
