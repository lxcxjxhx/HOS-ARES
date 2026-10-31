# 06 · 证据模型与 replay 规格

> 这是整个项目**最值钱的一块**。产品与 demo 的分界线就在这里：
> demo 说"我发现了一个洞"，产品能说"这是证据，你现在自己复现一遍"。
>
> **v3 变更**：
> 1. capsule 升 `1.1`，新增 `route` 块（地址从哪解析来的）。
> 2. replay 四态 → **六态**（新增 `ROUTE_UNRESOLVED` / `ROUTE_DRIFT`）。
> 3. 正文示例里**不再出现可运行的地址**（`27` N3）。

## 1. 三条原则

1. **内容寻址**：capsule 的 id 就是自身内容的 sha256，同样输入必然得到同样字节。
2. **可脱离 agent**：`replay` 不加载模型、不做推理，只重发请求并重算判定。
3. **只增不改**：sealed 之后不可变；要修就新建，旧的留作审计。

**v3 加一条前置原则**：

4. **地址不可推断**：capsule 存"实际发出的那次请求"（含地址，用于 replay）
   与"这个地址从哪来的"（`route` 块，用于可信度），**但不存任何可复用的地址常量**。
   replay 时重新解析，不复用旧地址。

## 2. capsule 完整 schema（`veripen.capsule/v1.1`）

> 示例中的 `<ROUTE_REF>` / `<TEMPLATE>` 是**文档占位**，运行时不出现：
> 真实值来自 `RouteSet`，且模板以 `template_sha256` 参与哈希，不落正文。

```json
{
  "capsule_version": "1.1",
  "capsule_id": "sha256:9f2c...",
  "finding_id": "01JAB...",
  "created_at": "2026-11-01T02:14:07Z",

  "oracle": {
    "id": "idor_cross_account",
    "version": "1.0.0",
    "cwe": "CWE-639",
    "params_hash": "sha256:3a1b..."
  },

  "route": {
    "routeset_sha256": "sha256:6d20...",
    "ref": "route://contract/7f3a2c11d9e04b58",
    "source": "contract",
    "sources_all": ["contract", "observed"],
    "confidence": 0.90,
    "template_sha256": "sha256:b1e7...",
    "resolver_version": "1.0.0",
    "resolved_at": "2026-11-01T02:13:58Z",
    "ttl_s": 900,
    "discovery_evidence_ids": ["ev_5c1a...", "ev_9d33..."]
  },

  "target": {
    "asset_id": "web-01",
    "frame_method": "GET",
    "host_key": "host_a",              // 不透明键，指向 assets 解析集合中的一项
    "resolved_ip": "172.30.0.5",
    "template_sha256": "sha256:b1e7...",   // 与 route.template_sha256 一致，便于交叉校验
    "userinfo_rejected": true
  },

  "env": {
    "profile_name": "demo-a",
    "profile_hash": "sha256:7ce4...",
    "image_digest": "sha256:dd11...",
    "runner_id": "worker-2@host-abc",
    "clock": { "t0": 1761966847, "t1": 1761966849, "timezone": "UTC" },
    "egress": { "mode": "whitelist", "proxy": null },
    "sandbox_overridden": false
  },

  "steps": [
    {
      "n": 1,
      "role": "baseline",
      "account": "primary",
      "route_ref": "route://contract/7f3a2c11d9e04b58",
      "request": {
        "method": "GET",
        "path_rendered_sha256": "sha256:aa12...",
        "path_rendered": "<ROUTE_REF 渲染结果，仅本地留档>",
        "headers": { "Cookie": "<redacted>", "Accept": "application/json" },
        "body": null,
        "body_sha256": null
      },
      "response": {
        "status": 200,
        "length": 412,
        "content_type": "application/json",
        "body_sha256": "sha256:ee01...",
        "body_excerpt_b64": "eyJpZCI6MiwidXNlcm5hbWUiOiJib2IifQ==",
        "excerpt_truncated": false,
        "persisted_ref": "out/tool-results/9c1f….txt",
        "persisted_size": 184320,
        "return_code_semantics": "ok"
      },
      "peer_ip": "172.30.0.5",
      "elapsed_ms": 37,
      "err": null
    },
    {
      "n": 2,
      "role": "probe",
      "account": "secondary",
      "route_ref": "route://contract/7f3a2c11d9e04b58",
      "request": {
        "method": "GET",
        "path_rendered_sha256": "sha256:aa12...",
        "headers": { "Cookie": "<redacted>" },
        "body": null
      },
      "response": {
        "status": 200, "length": 412,
        "body_sha256": "sha256:ee01...",
        "body_excerpt_b64": "eyJpZCI6MiwidXNlcm5hbWUiOiJib2IifQ=="
      },
      "peer_ip": "172.30.0.5",
      "elapsed_ms": 41,
      "err": null
    },
    {
      "n": 3,
      "role": "control",
      "account": "secondary",
      "route_ref": "route://contract/8c44be0a27f19d63",
      "note": "对照：访问自己无权拥有的资源应被拒绝",
      "request": { "method": "GET", "path_rendered_sha256": "sha256:cc90..." },
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
  "confidence_rule": "double_condition_satisfied",
  "route_strength": "contract",

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
| `route.*` | 证明地址不是猜的；决定结论的表述强度；`replay` 失败时能区分"环境变了"与"判断错了" |
| `route.confidence` / `route.source` | 报告措辞分级依据（`09 §3.1`）；低于门槛的绑定根本不该发请求 |
| `route.sources_all[]` | 同一条绑定可能有多来源佐证，replay 时优先用最稳的那个 |
| `route.template_sha256` | 目标改版探测：变了就是 `REPLAY_ROUTE_DRIFT` |
| `route.discovery_evidence_ids[]` | 反向审计：这次解析本身的证据在哪 |
| `env.profile_hash` / `image_digest` | 证据只在特定配置与镜像下成立，环境变了要标 stale |
| `env.clock` | 多源关联第一杀手是时钟；UTC 统一，另存 tz |
| `steps[*].role` | baseline/probe/control 三分类是"差分+对照"判定的输入 |
| `steps[*].route_ref` | 每一步各自打的是哪条绑定（对照通常是另一条） |
| `steps[*].request.path_rendered_sha256` | 可跨环境比对（不含真实地址），同时保留可比性 |
| `steps[*].request.path_rendered` | 本地 replay 需要；**导出时整字段删除**（见 §4） |
| `steps[*].peer_ip` | 证明打到的是范围内的 IP，不是 rebinding 后的黑洞 |
| `response.body_sha256` | 内容相同与否不靠人看，靠哈希 |
| `response.persisted_ref` / `persisted_size` | 4KB 截断后的完整内容在哪、有多大（`29 零件 B`）；导出时只留引用，不带正文 |
| `response.return_code_semantics` | 403 是"对照成功"还是"请求失败"，必须可审计（同上） |
| `env.sandbox_overridden` | 这一批动作里有没有在隔离外裸跑的（`29 零件 C`）；与"批准了什么"是两个不同的风险 |
| `response.body_excerpt_b64` | 人读需要看一点，压缩后 4KB 上限 |
| `assertion.fingerprint` | 把"为什么判定成立"变成结构化数据，可回归 |
| `confidence_rule` | 置信度必须可解释，不许模型给分 |
| `route_strength` | 判定依赖的最弱绑定档；决定报告措辞（`09 §3.1`） |
| `redaction.secrets_in_vault` | 复现要凭据，但凭据不能进证据包 |
| `chain.prev/self` | 哈希链，防事后篡改某一条 |

### 2.2 `path_rendered` 的双重身份（v3 特有）

| 场景 | 处理 |
|---|---|
| 本地 replay | 需要明文渲染结果 → 保留 |
| 导出/分享（`--external`） | **整字段删除**，只留 `path_rendered_sha256` |
| 报告正文 | 不出现，只给 `route_ref` 前 8 位 |
| bench 比对 | 用 `path_rendered_sha256`（跨环境可比） |

这条设计解决了一个真实矛盾：**证据要能复现（需要地址），证据要能分享（不能有地址）**。
v2 只有一个字段，所以只能在"能复现"和"能分享"里二选一。

## 3. 哈希链与防篡改

```
capsule_i.chain.prev_sha256 = capsule_{i-1}.chain.self_sha256
capsule_i.chain.self_sha256 = sha256( canonical_json(capsule_i 去掉 chain.self_sha256
                                                    与 steps[*].request.path_rendered) )
```

**v3 改动**：`path_rendered` **不参与哈希**。
理由：它包含环境相关信息（宿主、端口），参与哈希会导致"同一证据换环境就验不过"，
反而逼迫人们去篡改它。用 `path_rendered_sha256` + `route.template_sha256` 双锚点足够防篡改。

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
| **`path_rendered`** | **导出时整字段删除**；`path_rendered_sha256` 保留 |
| **`route.template`** | 只以 `template_sha256` 出现；导出时连 ref 都只给前 8 位 |
| **`RouteSet` 导出** | 需审批；导出条目只留 `{ref, source, confidence, template_sha256}`，无模板正文 |
| 用户自定义 | profile 可加 `redaction.extra_patterns[]` |
| 导出 | `--external` 时二次脱敏 + 水印 + `distribution` 标记 |

**可分享性验证**：把 capsule 发给第三方，他拿不到任何凭据、**也拿不到任何可用地址**，
但仍能看懂"打了什么（语义）、回了什么（哈希+摘录）、为什么判定成立"。
能复现他的环境（同一 profile + 同一资产注册）才能 replay——这是刻意设计的。

## 5. replay 语义（这是 A4 断言的全部内容）

```
veripen replay --capsule out/capsules/<id>.json [--profile demo-a] [--dry-run]
```

| 步骤 | 动作 | 失败输出 |
|---|---|---|
| 1 | 校验 `self_sha256` 与内容一致 | `CAPSULE_TAMPERED` |
| 2 | 校验 profile_hash / 当前 profile | `PROFILE_CHANGED`（不阻断，标 stale 后继续） |
| 3 | **按 capsule 的 `route.ref` + 当前 profile 重新解析绑定** | `REPLAY_ROUTE_UNRESOLVED` / `REPLAY_ROUTE_DRIFT` |
| 4 | guard 四处校验（scope 现在是否仍允许） | `REPLAY_BLOCKED`（**这是安全行为，不是失败**） |
| 5 | 从 vault 取凭据，按原顺序重发 steps（含 control） | `NETWORK_ERROR`（可 `--retry 2`） |
| 6 | 重算 `assertion.fingerprint` 与 verdict | `ASSERTION_NOT_REPRODUCED` |
| 7 | 写 `capsules.replay_status` + `replay_at`；判定不成立时转 `ASSERTION_NOT_REPRODUCED` 并标 `needs_human` | — |

**第 3 步是 v3 的核心改动**：replay **不复用 capsule 里的地址**，而是重新解析。
这带来两个好处：
- 目标改版后 replay 会说"这个面没了/变了"，而不是笼统的"未复现"；
- 分享出去的 capsule 不含可用地址，但持有者在自己环境里仍能 replay（只要 profile 里有同一资产注册）。

### 5.1 replay 结果的六种状态（不要只说"成功/失败"）

| 状态 | 含义 | 报告里怎么写 |
|---|---|---|
| `REPLAYED_OK` | 判定完全复现 | "已于 <时间> 复现" |
| `REPLAYED_PARTIAL` | 关键响应变了但结论仍成立 | "结论成立，响应细节已变化" |
| `ASSERTION_NOT_REPRODUCED` | 判定不成立 | "疑似已修复或环境差异，需人工复核" |
| `REPLAY_BLOCKED` | 当前 scope/审批不允许 | "按当前授权不可复现"（不视为缺陷） |
| **`REPLAY_ROUTE_UNRESOLVED`** | 该 `RouteRef` 在当前环境已无法解析（端点下线/契约变更/无对应面） | "该测试面在当前环境中已不存在"（**不视为已修复，也不视为缺陷**） |
| **`REPLAY_ROUTE_DRIFT`** | 解析到了不同模板（`template_sha256` 变） | "端点结构已变更，需人工确认后重判" |

第 4 种尤其重要：**它证明系统在"想复现但不该复现"时也会拦**。
第 5/6 种是 v3 新增：它们让 replay 在目标演进时给出**准确**结论，而不是把三种情况混成一个"未复现"。
这三种情况混在一起，正是外面那些"扫描器"被诟病的根源。

#### 5.1.1 六态的判定算法（按顺序短路，不许打乱）

顺序很重要：**先判能不能测，再判测得对不对**。反过来会把"不允许测"误记成"已修复"。

```python
def classify_replay(cap, result) -> ReplayState:
    # 0 完整性：内容未变（与网络无关，最先查）
    if not verify_self_sha(cap):
        raise CapsuleTampered()

    # 1 授权现状（能不能测）
    if not guard.scope_allows_now(cap.target, cap.route):
        return REPLAY_BLOCKED                       # 不是失败，是"不该测"

    # 2 路由现状（面还在不在）—— 必须先于"判定是否复现"
    fresh = resolve_routes(RouteQuery(ref=cap.route.ref, reason=STALE))
    if fresh.unresolved_for(cap.route.ref):
        return REPLAY_ROUTE_UNRESOLVED              # 面没了 ≠ 修好了
    if fresh.get(cap.route.ref).template_sha256 != cap.route.template_sha256:
        return REPLAY_ROUTE_DRIFT                   # 换了面

    # 3 重发并重判（面还在、结构未变）
    tr = resend_all(cap.steps)                      # 含 control
    if tr.network_failed:
        return REPLAYED_PARTIAL if tr.some_ok else raise NetworkError()

    verdict = cap.oracle.judge(pair_up(tr))
    if verdict == cap.verdict == VERIFIED and three_way_holds(pair_up(tr)):
        # 4 结论一致，再看细节是否也一致
        if all(s.response.body_sha256 == cap.steps[i].response.body_sha256
               for i, s in enumerate(tr.steps)):
            return REPLAYED_OK
        return REPLAYED_PARTIAL                     # 结论成立，细节变了
    return ASSERTION_NOT_REPRODUCED                 # 结论不成立
```

**三处顺序不可交换，各有代价**

| 若把 X 提到 Y 之前 | 后果 |
|---|---|
| 把 3（重发）提到 1（授权）之前 | 在"不该测"的场景里真的发出请求 → 越界 |
| 把 3 提到 2（路由）之前 | 端点已下线 → 404 → 判成 `ASSERTION_NOT_REPRODUCED` → **报告写"已修复"** |
| 把 4（细节比对）提到 3.5（三条件）之前 | 细节没变但对照失效（如目标整体变成 200）→ 判成 `REPLAYED_OK` → **假确认** |

第二行就是本次改版最想预防的那个错误。它的典型表现是：**漏洞没修，只是接口换了，报告却说修好了**。

#### 5.1.2 `REPLAYED_PARTIAL` 的精确边界

`PARTIAL` 太容易变成"什么都往里装"的垃圾桶。所以给它三条边界：

| 条件 | 是否 PARTIAL |
|---|---|
| 三条件成立，但 `body_sha256` 有任一不同 | ✅ |
| 三条件成立，但某步 `status` 从 200 变成 201（同语义） | ✅ |
| 三条件成立，但有步骤网络失败、其余成功 | ✅（并标 `network_partial: true`） |
| **三条件不成立** | ❌ 必须是 `ASSERTION_NOT_REPRODUCED` |
| **control 的网络失败** | ❌ 必须 `ASSERTION_NOT_REPRODUCED`（对照缺失即不可确认） |
| **只能测到部分步骤**（如 control 因权限 403 而不是预期的对照 403） | ❌ 必须 `ASSERTION_NOT_REPRODUCED` |

**最后两行的理由**：`PARTIAL` 的前提是**结论仍成立**；而"对照没跑成"意味着结论**未经检验**。
把它算成 PARTIAL，等于用一半的证据说"成立"。

### 5.2 六态与"已修复"的判定关系（重要）

```
REPLAYED_OK 且 observed 变成正常 → 已修复（可下结论）
ASSERTION_NOT_REPRODUCED 且 route 解析正常 → 疑似修复，需人工
REPLAY_ROUTE_UNRESOLVED → 无法判断（面没了 ≠ 修好了）
REPLAY_ROUTE_DRIFT → 无法判断（换了个面）
REPLAY_BLOCKED → 无法判断（不让你测）
```

**只有第一种能写"已修复"。** 这条纪律防止"端点下线被当成漏洞修好了"这种经典误报。

## 6. replay 的三种运行模式

| 模式 | 用途 | 特点 |
|---|---|---|
| `--dry-run` | 演示/审计 | 不发包，只校验链路与哈希，打印将发的请求（脱敏后） |
| 默认（实时） | 客户现场 | 真发请求，需要凭据与网络，**会重新解析路由** |
| `--offline-fixture` | 断网演示 | 用 capsule 内保存的响应重放判定函数，证明"判定是纯函数"（不解析路由） |

第三种是离线演示的兜底：模型、网络、靶场全挂，`make replay-offline` 依然能跑，因为它只调纯函数。
注意它与前两种的差别：**离线模式不做路由解析**，所以它证明的是"判定纯函数"，不是"目标仍然存在"。

## 7. 与报告的契约

| 规则 | 强制方式 |
|---|---|
| 报告每条发现必须带 `capsule_id` + `self_sha256` + `replay_status` + `route.ref`(前 8 位) + `route.source` | 渲染期断言，缺失直接抛异常（不降级、不跳过） |
| 报告头部写 `root_sha256`、`profile_hash` 与 `routeset_sha256` | 模板固定字段 |
| 未验证发现单独章节，且不得出现"确认/存在/高危"等措辞 | 措辞检查（`make lint-report`） |
| 同一 capsule 不得支撑两条不同结论的发现 | `ux_finding_run` + capsule 唯一引用 |
| **`route_strength ∈ {crawl, probe}` 的发现不得标 `high/critical`** | 渲染期断言（弱绑定不给高严重度） |
| **报告正文不得出现任何可网络定位字面量** | `make lint-noroute-report` |

## 8. 验收（B4 收工标准）

1. 靶场 3 条发现 → 3 个 capsule 文件 → 哈希链完整，每个含 `route` 块。
2. 手工改一个字节 → `replay` 报 `CAPSULE_TAMPERED`（演示"防篡改"）。
3. `replay --dry-run` 与实时模式都通过；`--offline-fixture` 在断网容器内通过。
4. 把 scope 改小后 `replay` 返回 `REPLAY_BLOCKED`（演示"该拦的时候拦得住"）。
5. **删掉一条绑定后 replay 返回 `REPLAY_ROUTE_UNRESOLVED`**（演示"面没了 ≠ 修好了"）。
6. **改 binding 的模板后 replay 返回 `REPLAY_ROUTE_DRIFT`**（演示"改版能识别"）。
7. `--external` 导出的 capsule 里搜不到任何地址（`grep -E 'https?://|[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+'` 为空）。
8. **顺序回归**（`tests/unit/test_replay_order.py`，三条）：
   - 端点下线 → `REPLAY_ROUTE_UNRESOLVED`（**不得**是 `ASSERTION_NOT_REPRODUCED`）
   - 模板变更 → `REPLAY_ROUTE_DRIFT`
   - 目标整体变成"任何 token 都 200" → `ASSERTION_NOT_REPRODUCED`（**不得**是 `REPLAYED_OK`）
9. **PARTIAL 边界**（`tests/unit/test_replay_partial.py`）：
   - control 网络失败 → `ASSERTION_NOT_REPRODUCED`
   - control 因权限 403（非预期对照 403）→ `ASSERTION_NOT_REPRODUCED`
   - body 变了但三条件成立 → `REPLAYED_PARTIAL`
