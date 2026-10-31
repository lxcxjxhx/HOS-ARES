# plan-veripen · 索引

> **这是一个可整体复制的规划包**：29 篇规格 + 5 份 JSON Schema + 3 个自检脚本，无外部依赖（仅需 `node`）。
>
> `plan-v3.0` · 2026-10-29 · 状态：`brainstorm-candidate`（等 driver 确认）
>
> **v3 一句话**：把"地址"从代码/配置/提示词/报告里彻底删掉，只留不透明引用。
> 上一版最大的结构缺陷不是漏了什么功能，是**允许任何地方写死路由**——而 agent 会把示例当契约抄。

## 0. 怎么用这个包（三步）

```bash
# 1 复制到新项目（整个文件夹一次拷走，不要只拷部分）
cp -r plan-veripen <project-root>/docs/plan
cp -r plan-veripen/schemas <project-root>/schemas

# 2 双重校验复制完整性（第二个现在包含零地址检查）
cd <project-root>/docs/plan
node check-plan.cjs          # 期望最后一行：structure OK
node _verify-manifest.cjs    # 期望最后一行：OK: N files verified

# 3 初始化权威链，然后按 21 的批次卡开工
node "<skill>/scripts/init-project-authority.mjs" \
  --root <project-root> --profile blueprint-set \
  --project-id veripen --title "VeriPen 渗透验证内核"
```

**复制后不要做的事**

| 不要 | 原因 |
|---|---|
| 把这 29 篇原样当执行权威 | 会变成"三份大文档互相抄"；按 `17 §1` 分流成 总设计案 / 蓝图集 / NEXT |
| 改文件名编号 | 批次卡里按编号引用（`01` `18` `21` `27`…），改名即断链 |
| 删 `check-plan.cjs` | 它是唯一的机械一致性检查，且现在串着零地址闸门 |
| **删 `check-noroute.cjs` 或加白让它变绿** | **那等于删掉唯一能阻止 hardroute 复活的闸门** |

**本包不含**（刻意如此）：代码、靶场镜像、数据集快照。代码按 `21` 从 B1 开始写；数据按 `26` 的政策获取。

## 包内清单（文件数/行数/字节以 `MANIFEST.md` 为准，避免两处数字打架）

```
plan-veripen/
├─ 00 … 26      27 篇规格（00 总纲 / 16 范围 / 21 批次 / 24 API / 25 靶场 / 26 数据政策 …）
├─ 27-无硬路由架构.md        ★ 六不变量 N1–N6、RouteRef/RouteSet、重试=重发现、lint 规则
├─ 28-逆向对标与借鉴规格.md  ★ REA 工作台、六抽取面、隔离铁律、借鉴产出格式
├─ 99-二期池.md  被推迟需求
├─ 29-逆向提取结果.md  ★ 实际拆出的零件清单（能拼/改后能拼/反面教材）
├─ README.md     本索引（含复制方法与阅读路线）
├─ MANIFEST.md   全部文件 sha256 清单 + 总字节/行数
├─ check-plan.cjs          结构自检（H1 / 围栏 / 交叉引用 / schema 解析 / ★零地址 / 版本一致）
├─ check-noroute.cjs       ★ 零地址闸门（27 N3 的可执行形式）
├─ noroute.allowlist       ★ 行级例外（上限 6 行，当前 0 行）
├─ _verify-manifest.cjs    sha256 逐个校验
└─ schemas/
   ├─ profile.schema.json            客户档（v2：零地址 + routes 段）
   ├─ capsule.schema.json            证据包（v1.1：含 route 块）
   ├─ finding.schema.json            发现 v2（VERIFIED 必须有 capsule_id + route_ref；弱绑定不得标高危）
   ├─ dataset-manifest.schema.json   数据溯源（source_ref 符号，禁 mock）
   └─ routeset.schema.json           ★ 绑定集（内容寻址、置信由规则算出）
```

**体积**：约 300 KB / 5,600 行（精确值见 `MANIFEST.md`）。**无外部依赖**（除 `node`，仅用于三个自检脚本）。

## 一句话

> 一份 `scope` + 一份客户档进来说明打谁、打到什么程度；
> **路由由 `route_discovery` 解析、判定由确定性 oracle 给出、LLM 只找路**；
> 每条发现产出可 `replay` 的 proof capsule；
> 换客户只换 YAML，内核不动，**地址不存在于任何静态文件里**。

## 范围口径（v3 修正）

| | v1.1（已废） | v2.x（已废） | **v3.0（现行）** |
|---|---|---|---|
| 原则 | 裁到最少能证明 | 范围不削，批次细化 | **范围不削 + 地址不入库** |
| oracle | 2 类 | 14 类 | **14 类（只见 RouteBinding）** |
| 路由 | 写死 | **写死（缺陷）** | **运行时解析（RouteRef/RouteSet）** |
| replay 态 | 成功/失败 | 四态 | **六态（含 ROUTE_UNRESOLVED / ROUTE_DRIFT）** |
| scope 校验 | 2 处 | 3 处 | **4 处（新增绑定门）** |
| 断言 | 5 条 | 8 条 | **9 条（A9 零地址）** |
| 批次 | 3 天 vibe | 17 批 | **18 批（新增 B1.5 路由层，前置）** |
| 逆向对标 | 无 | 无 | **有（28：六抽取面 + 隔离铁律）** |

理由：v2 的失败方式很具体——**每个"示例"都会变成代码里的常量**。
所以 v3 不靠"更严格的纪律"，靠**类型系统 + lint + bench 运行时校验**三重机械保证。

## 九条可演示断言（对外只讲这九条）

| # | 断言 | 命令 |
|---|---|---|
| A1 | 能卖第二家 | `make demo` / `make demo-alt` |
| A2 | 差异没漏进代码 | `make lint-noleak` |
| A3 | 内网能装 | `make package-offline && make doctor` |
| A4 | 不是"我说有洞" | `make replay ID=n`（七态） |
| A5 | 有数 | `make bench`（44 例 × 3 轮 + routeset 一致） |
| A6 | 覆盖够宽 | `make coverage-matrix`（14 类 × 资产 + 标签 × oracle） |
| A7 | 接口能自动覆盖 | `make cases CASES=contract` |
| A8 | 模型不是瓶颈 | `make bench MODEL=none` |
| **A9** | **地址不存在于代码** | **`make lint-noroute` + `make rediscovery-drill`** |

## 阅读路线

| 目的 | 读这篇 |
|---|---|
| **要知道为什么又改了一版** | **`27-无硬路由架构.md` §0（根因与判据）** |
| **要知道全量是什么** | `16-完整范围与交付地图.md` |
| **要知道怎么一批批做** | `21-实现批次与Agent执行计划.md`（注意 B1.5 在 B2 之前） |
| **要开跑了** | `15-开工提示词.md`（批次模板 A/B/B'/C/D） |
| 30 分钟看懂全局 | `00-总规划.md` |
| 评审架构 | `01` → `02` → `03` → **`27`** |
| 评审能力面 | `18`（14 类 + 标签字典）→ `19`（契约）→ `20`（认证） |
| 评审可信度 | `06` → `07` → `10` |
| 评审安全与合规 | `05` → `09` → `12` |
| 评审产品面 | `22`（人机协同）→ `23`（可观测，含审计第四问） |
| 评审排期与风险 | `13` → `14` → `17` |
| **想知道逆向该怎么用** | **`28-逆向对标与借鉴规格.md`** |
| **想知道拆出了什么能用的零件** | **`29-逆向提取结果.md`** |
| 担心被测什么 | `14 §7`（九块能力→本项目证据） |
| 要知道现在卡在哪 | `17 §3`（四态）/ `17 §6`（下一道门） |

## 全册目录（29 篇 + schemas，按卷）

### 卷 0 · 总纲

| 文件 | 内容 |
|---|---|
| `00-总规划.md` | 定位、九断言、边界、4 波 18 批里程碑、交付物、人天、术语表 |
| `16-完整范围与交付地图.md` | **13 项能力全表、9 条断言、Core Spine 8 条、成长轨道、Parking Lot** |
| `17-权威链与证据门.md` | **★ 九条设计不变量（§0）**、权威三层与分流、蓝图档位、四态账本、证据四类 |
| **`27-无硬路由架构.md`** | **★ 根因与判据、六不变量 N1–N6、RouteRef/RouteSet/RouteBinding、五来源、重试=重发现、lint 规则、反例库、A9 验收** |

### 卷 1 · 架构

| 文件 | 内容 |
|---|---|
| `01-包结构与内核契约.md` | 四包分层、依赖铁律、Oracle 协议（只见绑定，无 `base_url`）、六契约测试 |
| `02-客户档Profile规格.md` | profile v2 全字段（含 `routes` 段）、零地址校验、两个示例档 |
| `03-数据模型与状态机.md` | 8 张表 DDL、任务/发现/capsule 状态、10 条一致性自检（含路由卫生 5 条） |
| **`28-逆向对标与借鉴规格.md`** | **★ 隔离铁律 Q1–Q4、六抽取面 F1–F6、目标清单、REA 工作台、借鉴产出五段格式** |

### 卷 2 · 运行时

| 文件 | 内容 |
|---|---|
| `04-调度规格.md` | 幂等三层闸门、租约心跳、**四分类错误（含 ROUTE）**、背压、死信、五预算 |
| `05-安全边界规格.md` | **scope 四处校验** + 9 类绕过面 + 令牌桶 + **三类审批门** + 凭据 + 出口 |
| `06-证据与replay规格.md` | capsule v1.1（含 `route` 块）、哈希链（`path_rendered` 不入哈希）、脱敏、**replay 六态** |
| `08-Agent循环规格.md` | **discovery 三档降级** + Oracle 驱动 ReAct、`list_routes`/`rediscover_routes`、四层上下文、七条隔离保证 |

### 卷 3 · 能力域

| 文件 | 内容 |
|---|---|
| `07-Oracle规格.md` | 通用契约 + **判定骨架（8 个冻结函数 + three_way_holds）** + Transform 语义 + 载荷模板 + 4 个基类 + 能力边界 |
| `18-Oracle目录（14类判定矩阵）.md` | **14 类完整目录（A6/B5/C3）+ 18 个语义标签字典** + 默认启用策略 |
| `19-接口契约与用例生成.md` | 契约 → OperationSet → **打标签（三种来源）** → RouteSet（不产路径） |
| `20-认证体系测试点.md` | JWT 10 / OAuth2 8 / mTLS 7 / Session 5 / SAML 清单（issuer/redirect 用符号） |

### 卷 4 · 工程与质量

| 文件 | 内容 |
|---|---|
| `09-报告与交付格式.md` | 三视图、ruleId、**双指纹**、**证据强度分级**、措辞纪律、水印、零地址 |
| `10-评测协议.md` | 44 例固定集、**14 指标（含路由一致性/解析率）**、判读规则、反作弊 |
| `11-测试策略与靶场用例.md` | **四层测试（L0 路由）**、L1 清单、44 例全表（标签+变换族）、第五种结局 |
| `12-工程与交付.md` | 三档部署、离线包、**doctor 16 项**、resolver 版本化、profile v1→v2 失败式迁移 |
| `23-可观测性与诊断.md` | **stats 四表（含重发现轨迹）**、日志不记 url、**审计第四问**、埋点 |
| `24-服务API与错误码.md` | **op_id 表（不是路径）** + bindings 契约 + 44 个错误码 + 35 个 make 目标 |
| `25-靶场与真实数据规格.md` | 5 个真实靶场 + tsp-lab、**标签适配契约**、10 缺陷按语义描述、10 安全孪生 |
| `26-数据真实性与来源政策.md` | 禁用 mock：三种允许来源、`source_ref` 溯源、机械检查 |

### 卷 5 · 执行

| 文件 | 内容 |
|---|---|
| `15-开工提示词.md` | **批次模板 A/B/B'/C/D** + 全量总纲 + 一页速查 + 五条纪律 |
| `21-实现批次与Agent执行计划.md` | **18 批批次卡 + 依赖图 + 3 lane 并行 + 批次账本** |
| `22-产品与人机协同.md` | 只读控制台（op_id 页面）、降噪队列（含弱证据分档）、审批界面 |
| `13-风险登记册.md` | P0/P1/P2 风险 + **R6c 硬路由复活** + 停止条件 + 变更记录 |
| `14-二期路线.md` | 五期路径、**`core diff=0` 守则 7 条**、合同→配置映射、竞争话术 |
| `99-二期池.md` | 被推迟需求与前置条件 |

## 机器可校验的产物

```
schemas/profile.schema.json            客户档（v2：零地址 + routes 段）
schemas/capsule.schema.json            证据包 v1.1（route 块 + path_rendered 双身份）
schemas/finding.schema.json            发现 v2（VERIFIED 必须有 capsule_id + route_ref；弱绑定不得标高危）
schemas/dataset-manifest.schema.json   数据溯源（source_ref 符号）
schemas/routeset.schema.json           ★ 绑定集（ref 格式、置信范围、标签枚举、unresolved）
check-noroute.cjs                      ★ 零地址闸门（6 条规则 + 整目录/文件豁免 + allowlist ≤6）
```

建项目后 `cp -r schemas <project>/schemas/`，agent 从 schema 生成 pydantic 模型，**不靠人抄字段**。

## 四个不许动的决定

1. **判定是纯函数**（`oracle.judge` 无 IO/时钟/随机）—— 可信度、可测性、离线演示全挂在它上面。
2. **三条件合取才出 VERIFIED**（probe ∧ baseline ∧ control）—— FP 门槛 0 的唯一实现方式。
3. **客户差异只进 profile** —— `core diff = 0` 是"能卖第二家"的机械判据。
4. **★ 地址只能由 `route_discovery` 产出** —— 六不变量 N1–N6；这是"不腐化"的唯一机械保证。

前三条是可信度，第四条是**寿命**。少第四条，前三条早晚会在一次"顺手"里失效。

## 现在没有卡住的了

v3 把所有 OPEN 都改成**默认决策 + 改判成本**（`17 §3`）：

| # | 默认 | 改判成本 |
|---|---|---|
| O1 | demo-b 用契约解析（手写已不可选） | 半天 |
| O2 | 报告中文 | 2 小时 |
| O3 | compose 变量切：默认 SQLite，交付 PG | 1 小时 |
| O4 | 审批门演示拒绝 + 批准各一次 | 30 分钟 |
| O5 | Ollama 容器，`VERIPEN_MODEL` 可切网关 | 1 小时 |
| O6 | 控制台给脱敏 capsule 下载（无地址） | 30 分钟 |
| **O7** | **`probe` 源做受审子集，默认关闭** | 关一个开关 |
| **O8** | **`crawl` 源做只读 HTML，不跑 JS** | 关一个来源 |
| **O9** | **allowlist 上限 6 行** | 调高需记录理由 |

开工照默认走；你要改，说一句即可。

## 自检

```bash
node check-plan.cjs        # 最后一行 structure OK（内含零地址闸门）
node check-noroute.cjs     # 单独跑闸门：最后一行 OK: 0 命中
node _verify-manifest.cjs  # 最后一行 OK: N files verified
```

`check-plan.cjs` 校验七件事：每个 md 有 H1、代码围栏成对、`NN-xxx` 交叉引用不悬空、
`schemas/*.json` 可解析、**零地址 0 命中**、**标签字典三处一致**、**版本号在 `00` 与 `README` 之间一致**。

| 检查 | 挡什么 |
|---|---|
| 零地址闸门 | 硬路由复活（v3 新增） |
| **标签字典三处一致** | **`18 §6` / `routeset.schema` / `profile.schema` 的 18 个标签漂移（v3 新增）** |
| **每个 oracle 的 `selector_tags` 都已登记** | **写了不存在的标签（v3 新增，实测能抓 `url_fetchh`）** |
| 版本号一致 | 两份文档各写一个版本号这种静默腐化（v3 新增） |

后三条都是"语义约定也要能被机器验证"。文档里写着"18 个标签"，代码里实际是 17 个——
这种漂移人眼看不出来，但会让 B8 的 parser 在运行时才炸。

## 上游材料（不在本包内，仅溯源）

| 材料 | 原位置 | 说明 |
|---|---|---|
| 三域能力地图 | `projects/2026-10-AI-PENTEST-TOOLS/29-三域能力地图与项目方案.md` | 本册 Part 1 的来源 |
| 开源渗透 Agent 生态调研 | 本仓 `docs/research/01-open-source-pentest-agent-survey.md` | `28` 的目标清单依据（star/license/活跃度原始字段） |
| 面试支撑 | 同目录 `20`–`28` | 讲稿、深聊、答题框架 |
| 工具集调研 | 同目录 `tools/`、`_archive/docs/` | 19 篇工具分析 |
| v1.0 快照 | `_archive/docs/plan-veripen-v1-baseline/` | 对比用（v1 是裁剪版，已废） |

> 路径是相对**原工作区**的，拷进新项目后这些链接会失效——这是刻意的：
> 新项目只依赖本包，不依赖旧仓。
>
> **逆向证据目录**（`docs/research/re/`）是 `check-noroute` 的**唯一整目录豁免**。
> 它只能放样本证据，不能放任何被代码引用的东西（`28 §0` Q1）。
