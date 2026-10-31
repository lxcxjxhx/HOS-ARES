# HOS-ARES 生态移植与集成地图（v1）

> 目的：把用户点名的开源 Harness / 安全 Agent 生态**有据可查地拉进项目**，并把每个工具映射到
> 「借鉴什么 → 落到哪篇规格 / 哪个批次 / 哪条不变量」。
> 本文遵守 `plan-veripen/28-逆向对标与借鉴规格.md` 的隔离铁律 Q1–Q4：
> 源码只用于**结构研究**，任何 URL/host/路径常量只落在 `docs/research/re/`，不进代码。
>
> 拉取方式：`bash scripts/vendor-fetch.sh`（读 `vendor.lock.json`，逐 commit 从官方仓库取 tarball，可复现）。
> 源码目录 `vendor/` 已 gitignore，不入 git；仓库只保留 pin 与脚本。

## 0. 一页结论

| 层 | 我们已有 | 本轮补齐 | 对标绿盟 HARNESS 的差距 |
|---|---|---|---|
| 判定内核 | 纯函数 oracle + 三条件合取 + 六态 replay（`06/07`） | 不变 | ✅ 领先 |
| 零硬路由 | RouteRef/RouteSet + lint（`27`） | 不变 | ✅ 领先（F7） |
| 控制平面 | 只有 agent 循环（`08 §1`） | 从 Strix / cyber-harness 抽「Conductor 职责边界」 | 结构性缺失 → 补 |
| 状态黑板 | 只有 facts ledger | 从 PyRIT(orchestrator)/Strix(memory) 抽「只增不改事实板」 | 结构性缺失 → 补 |
| Playbook 资产化 | 只有 14 类 oracle | 从 garak/deepteam 探针目录 + cyber-harness 扩展点抽「打法资产」格式 | 结构性缺失 → 补（最高价值） |
| 上下文压缩 | 四层裁剪，无压缩算法 | 从 Strix `output_store` head+tail 抽保留策略 | 算法缺 → 补 |
| 智能体自身安全 | scope/审批/出口（`05`） | 从 AI-Infra-Guard(Agent/Skills/MCP 扫描) + promptfoo 红队抽「上下文污染检测」 | 部分缺失 → 补 |
| 评测 | 44 例 bench（`10/11`） | 从 promptfoo「配置即用例」+ garak 探针分类抽用例组织 | 接近 → 增强 |

## 1. 移植目标清单（`vendor.lock.json` 为准）

| slug | 仓库 | 许可 | 面 | 借鉴什么（结构，非内容） | 落地 |
|---|---|---|---|---|---|
| `rea` | morluto/rea | MIT | F1 F6 | 逆向工作台的**工具/权限门控**与**证据包/replay**形态；同时作为本题 REA 工作台运行底座 | `28 §3` 工作台协议；`06` capsule |
| `strix` | usestrix/strix | Apache-2.0 | F1 F3 F6 | ① `output_store` head+tail+落盘+读回指引 ② `BudgetPolicy.pause` ③ 工具注册/审批门控位置 ④ PoC 验证缺 control 的失败点 | `08 §2`/`04 §7`/`05 §3`；`30 §5` |
| `promptfoo` | promptfoo/promptfoo | MIT | F5(评测) | YAML「配置即用例」+ redteam provider/grading + OWASP/MITRE 映射组织 | `11` 用例表 / `10` 评测协议 |
| `garak` | NVIDIA/garak | Apache-2.0 | F5(探针) | 100+ 探针→标签的分类法，补 `18` 标签字典来源 | `18 §6` 标签字典 |
| `ai-infra-guard` | Tencent/AI-Infra-Guard | Apache-2.0 | F1 F2 | Agent/Skills/MCP/基础设施扫描面划分；jailbreak 评估结构 | `24` MCP 暴露面 / `05` 自身安全 |
| `pyrit` | microsoft/PyRIT | MIT | F3 | orchestrator 的 **converter/scorer 分离** + 多轮策略表示（Crescendo/TAP 仅作形状参考） | `08` 多轮策略（二期） |
| `deepteam` | confident-ai/deepteam | Apache-2.0 | F5(分类) | 50+ 漏洞类型分类维度，校核 `18` 目录完备性 | `18` 目录分类 |
| `cyber-harness` | chainreactors/cyber-harness | AGPL-3.0 | F1 F3 | 同为 cyber 场景通用 Harness 的**扩展式架构/工具总线**（比 Strix 更贴）；AGPL → **只读结构，不复制代码** | `08 §1` 控制平面 / `01` 工具边界 |
| `wshobson-agents` | wshobson/agents | MIT | — | 开发期 skill 库（评审/调试/安全/测试/编排），降低常见开发与规划失误 | `.dsh/skills/`（见 §4） |

> 许可纪律：AGPL 的 `cyber-harness` 只做**结构阅读**，不把代码搬进 `veripen_*`；
> 其余 Apache/MIT 可借鉴实现细节但仍按 `28 §5` 七段式产出、不复制字符串。

## 2. 对标绿盟 AI-PTS 2.0（驭智·HARNESS）的三道结构性缺口 → 用谁补

依据 `30-对标绿盟HARNESS与开源生态.md §2`，补三件工程设施，全部可在现有 18 批内消化：

| 缺口 | 补什么 | 从哪个移植目标取形 | 落点 |
|---|---|---|---|
| 控制平面 | Conductor 只产计划不产结论（I4） | `cyber-harness` 扩展点 + `strix` BudgetPolicy | `08 §1.2` / B10 |
| 状态黑板 | 只增不改、存事实+出处、冲突标 `disputed`（I10） | `strix` memory + `pyrit` orchestrator 状态 | `03` facts 表 / B2,B10 |
| Playbook 资产化 | `playbooks/<id>.yaml` 纯数据、只能调 oracle | `garak`/`deepteam` 探针目录结构 + `cyber-harness` 审计流 | `31` + schema / B7,B9 |
| 上下文污染防护 | 4 条规则 + 注入模式检测 | `ai-infra-guard` agent 扫描 + `promptfoo` 红队用例 | `05`/`08 §4` / B3,B10 |

> 对外定位（`30 §8` 不变）：绿盟卖「平台+专家知识+多智能体=生产力」，我们卖「可验证内核+零硬路由+可 replay=可信度」。
> 移植生态是为了**补平台设施**，不是为了在功能数量上追平。

## 3. 抽取与产出规范（每个目标照 `28 §3` 执行）

```
0  docs/research/re/<slug>/{manifest.json,evidence/,notes.md,extracts/}
1  inspect_* 取 sha256/架构/打包形态 → manifest.json
2  list_strings(summary) 按 28 §3.1 分类计数（不导出原文）+ list_procedures(summary) 建索引
2b 先 grep 调用点，再读实现（零调用的“安全模块”是最高价值发现）
3  按 F1–F6 选 1–2 面做定点分析 → 结构描述写入 notes.md
4  需要时导出 evidence bundle 到 evidence/
5  extracts/<n>-<主题>.md 固定七段，必须写“对应不变量编号”
6  close_binary；manifest.json 记 sha256+日期+置信度
```

**当前状态**：源码已按 commit 拉齐（`vendor.lock.json`）；抽取按批次读（`28 §7`：B1–B4 不读，B1.5 起读 F1）。

## 4. 技能库同步（`.dsh/skills/`）

- 源：`wshobson/agents @ 0874a27b`（MIT）。
- 脚本：`bash scripts/sync-skills.sh`（幂等；读 `vendor/wshobson-agents`，denylist 排除离域插件）。
- 命名：`<plugin>--<skill>`，含 `SKILL.md` 与 `references/`。
- 清单与排除理由见 `.dsh/skills/MANIFEST.md`。

## 5. 复现与校验

```bash
node -e 'require("./vendor.lock.json").targets.forEach(t=>console.log(t.slug,t.commit))'
bash scripts/vendor-fetch.sh            # 拉全部（已存在则跳过）
bash scripts/sync-skills.sh             # 同步 skill
du -sh vendor/*                          # 各目标体积
```

## 6. 与批次的关系（不新增批次）

| 批次 | 何时读哪个移植目标 | 产出 |
|---|---|---|
| B1.5 Route Layer | `rea`、`ai-infra-guard`（工具→路由解析/暴露面） | `extracts/01-tool-schema.md` |
| B10 agent 循环 | `strix`、`cyber-harness`、`pyrit` | `extracts/02-loop-gating.md`、`03-planning.md` |
| B7/B9 Playbook | `garak`、`deepteam`、`cyber-harness` | `extracts/04-playbook.md` |
| B12 bench | `promptfoo` | `extracts/05-eval-cases.md` |
| 二期 | `pyrit` 多轮编排 | `99` P-10 |

> 与 `28 §7` 一致：内核不变量先由自有需求冻结，逆向只做**校准**，不做**定义**。
