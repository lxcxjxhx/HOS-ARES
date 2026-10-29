# plan-veripen · 索引

> **这是一个可整体复制的规划包**：27 篇规格 + 4 份 JSON Schema + 2 个自检脚本，无外部依赖（仅需 `node`）。
>
> `plan-v2.2` · 2026-10-29 · 状态：`brainstorm-candidate`（等 driver 确认）

## 0. 怎么用这个包（三步）

```bash
# 1 复制到新项目（整个文件夹一次拷走，不要只拷部分）
cp -r plan-veripen <project-root>/docs/plan
cp -r plan-veripen/schemas <project-root>/schemas

# 2 双重校验复制完整性
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
| 把这 28 篇原样当执行权威 | 会变成"三份大文档互相抄"；按 `17 §1` 分流成 总设计案 / 蓝图集 / NEXT |
| 改文件名编号 | 批次卡里按编号引用（`01` `18` `21`…），改名即断链 |
| 删 `check-plan.cjs` | 它是唯一的机械一致性检查 |

**本包不含**（刻意如此）：代码、靶场镜像、数据集快照。代码按 `21` 从 B1 开始写；数据按 `26` 的政策获取。

## 包内清单（文件数/行数/字节以 `MANIFEST.md` 为准，避免两处数字打架）

```
plan-veripen/
├─ 00 … 26      27 篇规格（00 总纲 / 16 范围 / 21 批次 / 24 API / 25 靶场 / 26 数据政策 …）
├─ 99-二期池.md  被推迟需求
├─ README.md     本索引（含复制方法与阅读路线）
├─ MANIFEST.md   34 文件 sha256 清单 + 总字节/行数
├─ check-plan.cjs          结构自检（H1 / 围栏 / 交叉引用 / schema 解析）
├─ _verify-manifest.cjs    sha256 逐个校验
└─ schemas/
   ├─ profile.schema.json            客户档
   ├─ capsule.schema.json            证据包（steps/redaction/chain）
   ├─ finding.schema.json            发现（VERIFIED 必须有 capsule_id）
   └─ dataset-manifest.schema.json   数据溯源（禁 mock 的机械保证）
```

**体积**：约 254 KB / 4,900 行（精确值见 `MANIFEST.md`）。**无外部依赖**（除 `node`，仅用于两个自检脚本）。

## 一句话

> 一份 `scope.yaml` + 一份客户档进来说明打谁、打到什么程度；确定性 oracle 验证，LLM 只找路；
> 每条发现产出可 `replay` 的 proof capsule；换客户只换 YAML，内核不动。

## 范围口径（v2 修正）

| | v1.1（已废） | **v2.0（现行）** |
|---|---|---|
| 原则 | 裁到最少能证明 | **范围不削，批次细化** |
| oracle | 2 类 | **14 类**（A 6 / B 5 / C 3） |
| 交付 | MD 报告 | MD + SARIF + JSON + 只读控制台 |
| 规模 | 1 worker | 3 worker + 死信 + 审批门 |
| 断言 | 5 条 | **8 条** |
| 粒度 | 3 天 vibe | **17 批 × 独立 DoD** |
| 工期 | 3 天 | 8.5–13 个工作日（3 lane 并行 / 串行） |

理由：agent 写代码快，**瓶颈在验证与集成**；所以不砍功能，砍批次粒度。

## 八条可演示断言（对外只讲这八条）

| # | 断言 | 命令 |
|---|---|---|
| A1 | 能卖第二家 | `make demo` / `make demo-alt` |
| A2 | 差异没漏进代码 | `make lint-noleak` |
| A3 | 内网能装 | `make package-offline && make doctor` |
| A4 | 不是"我说有洞" | `make replay ID=n` |
| A5 | 有数 | `make bench`（44 例 × 3 轮） |
| A6 | 覆盖够宽 | `make coverage-matrix`（14 类 × 资产） |
| A7 | 接口能自动覆盖 | `make cases CASES=openapi.yaml` |
| A8 | 模型不是瓶颈 | `make bench MODEL=none` |

## 阅读路线

| 目的 | 读这篇 |
|---|---|
| **要知道全量是什么** | `16-完整范围与交付地图.md` |
| **要知道怎么一批批做** | `21-实现批次与Agent执行计划.md` |
| **要开跑了** | `15-开工提示词.md`（批次模板 A/B/C/D） |
| 30 分钟看懂全局 | `00-总规划.md` |
| 评审架构 | `01` → `02` → `03` |
| 评审能力面 | `18`（14 类）→ `19`（契约）→ `20`（认证） |
| 评审可信度 | `06` → `07` → `10` |
| 评审安全与合规 | `05` → `09` → `12` |
| 评审产品面 | `22`（人机协同）→ `23`（可观测） |
| 评审排期与风险 | `13` → `14` → `17` |
| 担心被测什么 | `14 §7`（八块能力→本项目证据） |
| 要知道现在卡在哪 | `17 §3`（四态）/ `17 §6`（下一道门） |

## 全册目录（28 篇 + schemas，按卷）

### 卷 0 · 总纲

| 文件 | 内容 |
|---|---|
| `00-总规划.md` | 定位、八断言、边界、4 波 17 批里程碑、交付物、人天、术语表 |
| `16-完整范围与交付地图.md` | **12 项能力全表、8 条断言、Core Spine 7 条、成长轨道、Parking Lot** |
| `17-权威链与证据门.md` | 权威三层与分流映射、蓝图档位、四态账本、证据四类、review 结论 |

### 卷 1 · 架构

| 文件 | 内容 |
|---|---|
| `01-包结构与内核契约.md` | 三包分层、依赖铁律、目录树、Oracle 协议、模块职责、契约测试 |
| `02-客户档Profile规格.md` | profile v1 全字段、8 条语义校验、两个示例档、profile_hash、新增客户 SOP |
| `03-数据模型与状态机.md` | 6 张表 DDL、任务 8 态、发现 5 态、容量预算、5 条一致性自检 |

### 卷 2 · 运行时

| 文件 | 内容 |
|---|---|
| `04-调度规格.md` | 幂等三层闸门、租约心跳、错误分类学、背压、死信、预算刹车 |
| `05-安全边界规格.md` | scope 三处校验 + 6 类绕过面、令牌桶 Lua、审批门、凭据、出口、留痕 |
| `06-证据与replay规格.md` | capsule schema、哈希链、脱敏、replay 四态三模式、报告契约 |
| `08-Agent循环规格.md` | Oracle 驱动 ReAct、工具 schema、四预算、上下文三层、降级链、隔离保证 |

### 卷 3 · 能力域

| 文件 | 内容 |
|---|---|
| `07-Oracle规格.md` | **通用契约** + 4 个基类详解（其余 10 类按同契约扩展） |
| `18-Oracle目录（14类判定矩阵）.md` | **14 类完整目录**（A6/B5/C3）+ 判定矩阵 + 默认启用策略 + 插件清单 |
| `19-接口契约与用例生成.md` | OpenAPI/GraphQL 解析 → 5 类模板 → 打分去重 → 稳定输出 |
| `20-认证体系测试点.md` | JWT 10 / OAuth2 8 / mTLS 7 / Session 5 / SAML 清单 + 覆盖矩阵 |

### 卷 4 · 工程与质量

| 文件 | 内容 |
|---|---|
| `09-报告与交付格式.md` | 三视图、ruleId、fingerprint、MD 模板、合规映射、水印、质量分 |
| `10-评测协议.md` | 44 例固定集、11 指标、判读规则、回归阈值、人工基线、反作弊 |
| `11-测试策略与靶场用例.md` | 三层测试、L1 清单、**44 例全表**（基础 20 + 扩展 24） |
| `12-工程与交付.md` | 三档部署、离线包、doctor 15 项、配置管理、版本灰度、日志 |
| `23-可观测性与诊断.md` | stats 三表、日志规范、doctor 说人话、指标埋点、可观测承诺 |
| `24-服务API与错误码.md` | **HTTP API 全表**（任务/发现/证据/审批/观测/控制台）+ **34 个错误码** + **25 个 make 目标规格** |
| `25-靶场与真实数据规格.md` | **5 个真实开源靶场**（DVWA/juice/crAPI/vAmPI/WebGoat）+ `tsp-lab` 适配层、10 条故意缺陷 + 10 条安全孪生、OOB、靶场自测 |
| `26-数据真实性与来源政策.md` | **禁用 mock**：三种允许来源（真实靶场 / 公开数据快照 / 受管拟真集）、录制回放夹具、provenance manifest、机械检查 |

### 卷 5 · 执行

| 文件 | 内容 |
|---|---|
| `15-开工提示词.md` | **批次模板 A/B/C/D** + 全量总纲 + 一页速查 + agent 三条纪律 |
| `21-实现批次与Agent执行计划.md` | **17 批批次卡 + 依赖图 + 3 lane 并行 + 批次账本 + 工期** |
| `22-产品与人机协同.md` | 只读控制台、降噪队列、审批界面、反馈闭环、培训物料 |
| `13-风险登记册.md` | P0/P1/P2 风险 + 触发信号 + 停止条件 + 6 个 OPEN + 变更记录 |
| `14-二期路线.md` | 五期路径、`core diff=0` 守则、合同→配置映射、竞争话术、能力对齐 |
| `99-二期池.md` | 14 条被推迟需求与前置条件 |

## 机器可校验的产物

```
schemas/profile.schema.json    客户档 schema（pydantic 可直接生成模型）
schemas/capsule.schema.json    证据包 schema（含 chain / redaction / steps 约束）
schemas/finding.schema.json    发现 schema（VERIFIED 必须有 capsule_id 的 if/then 约束）
schemas/dataset-manifest.schema.json  数据集溯源（source/license/sha256/rows/pii，llm-synthetic 需 seed+reviewed_by）
```

建项目后 `cp -r schemas <project>/schemas/`，agent 从 schema 生成 pydantic 模型，**不靠人抄字段**。

## 三个不许动的决定

1. **判定是纯函数**（`oracle.judge` 无 IO/时钟/随机）—— 可信度、可测性、离线演示全挂在它上面。
2. **三条件合取才出 VERIFIED**（probe ∧ baseline ∧ control）—— FP 门槛 0 的唯一实现方式。
3. **客户差异只进 profile** —— `core diff = 0` 是"能卖第二家"的机械判据。

## 现在没有卡住的了

v2.1 把 6 个 OPEN 全部改成**默认决策 + 改判成本**（`17 §3`）：

| # | 默认 | 改判成本 |
|---|---|---|
| O1 | 复用 B8 的 OpenAPI 解析器 | 半天 |
| O2 | 报告中文 | 2 小时 |
| O3 | compose 变量切：默认 SQLite，交付 PG | 1 小时 |
| O4 | 审批演示拒绝 + 批准各一次 | 30 分钟 |
| O5 | Ollama 容器，`VERIPEN_MODEL` 可切网关 | 1 小时 |
| O6 | 控制台给脱敏 capsule 下载 | 30 分钟 |

开工照默认走；你要改，说一句即可。

## 自检

```bash
node check-plan.cjs        # 最后一行 structure OK
node _verify-manifest.cjs  # 最后一行 OK: N files verified
```

校验四件事：每个 md 有 H1、代码围栏成对、`NN-xxx` 交叉引用不悬空、`schemas/*.json` 可解析。

改完任何一篇都跑一次；它挡的是"文档之间互相引用到不存在的文件"这类静默腐化。

## 上游材料（不在本包内，仅溯源）

| 材料 | 原位置 | 说明 |
|---|---|---|
| 三域能力地图 | `projects/2026-10-AI-PENTEST-TOOLS/29-三域能力地图与项目方案.md` | 本册 Part 1 的来源 |
| 面试支撑 | 同目录 `20`–`28` | 讲稿、深聊、答题框架 |
| 工具集调研 | 同目录 `tools/`、`_archive/docs/` | 19 篇工具分析 |
| v1.0 快照 | `_archive/docs/plan-veripen-v1-baseline/` | 对比用（v1 是裁剪版，已废） |

> 路径是相对**原工作区**的，拷进新项目后这些链接会失效——这是刻意的：
> 新项目只依赖本包，不依赖旧仓。
