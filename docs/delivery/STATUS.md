# HOS-ARES 状态总账

> **最后更新**：2026-11-20（账本 502 批）。这份文件不带日期后缀——它随仓库一起演进，历史快照看 git log 或 `docs/delivery/final-acceptance-*.md`。

一句话：**判定内核、三支柱、交付形态、界面与成片都已闭合且有证据；剩下的每一件都需要一个口径或一台装了东西的机器。**

这份文件只写**能当场复核的数字**。每条都给了命令，跑一遍就能对上。

---

## 1. 有什么

### 判定能力

| 项 | 数量 | 怎么验 |
|---|---|---|
| oracle | 31 条（元数据齐全、id 唯一） | `hos-ares parity` 的 V1 |
| 认证判定 | 15 条 auto 全部有判定且可跑 | `hos-ares coverage` |
| 覆盖矩阵 | 14 类标签 × 用例，空类显式列出 | `hos-ares coverage` |
| bench 门槛 | recall ≥0.90 / precision =1.00 / 越界 0 / error 0 | `pytest tests/integration/test_bench_gates.py` |

### 三个支柱（都能端到端跑）

| 支柱 | 命令 | 验过什么 |
|---|---|---|
| 渗透 | `hos-ares run` | 契约 → oracle → capsule → 报告 |
| 发现 | `hos-ares discover` | 无契约从首页攒面（不产判定） |
| 平台 | `hos-ares platform` | 多资产 + 审批阻断 + 汇总报告 |
| Agent | `hos-ares chat` | 计划 + 工具流 + diff（规则模式） |
| └ codex 式能力 | 见下 | **一次多工具调用 · 多文件 patch · MCP client · 流式收包**，四项各有用例 |

### 交付形态

| 形态 | 状态 |
|---|---|
| console script | `hos-ares --version` → `0.1.0`（`--help` 全用连字符） |
| wheel | `pip wheel .` → 231 文件，含 `hos_ares.{bench,doctor,report}` 全部子包 |
| exe | `out/dist/HOS-ARES.exe` → `usage: hos-ares-desktop` |
| 安装包 | Inno Setup 脚本在位（`PrivilegesRequired=admin`，需管理员） |
| 成片 | `out/demo/hos-ares-tour.mp4` 62.9s / 1280×720 / H.264 |

---

## 2. 验收证据（全绿，2026-11-20 实测）

```
tests/integration                 454 项   0 失败   exit=0（约 78 分钟）
tests/routes + lab + fixtures      66 项   0 失败
tests/unit                       2128 项   0 失败（1 skip：Windows 符号链接）
verify                             13 项   全绿（含 mypy / 五道 lint / 账本 / 结构）
doctor                       21 ok / 0 fail / 4 skip
hos-ares parity               22 项：green 21 / 未验 1 / red 0
```

一条命令复现闸门：

```bash
python scripts/verify.py --quick-tests    # 13 项，约 2 分钟（含单测）
```

`parity` 里那 1 项未验是 **E1 执行隔离**——本机没有 docker/podman，判不了。

---

## 3. 已知边界（不隐瞒）

| 边界 | 性质 | 影响 |
|---|---|---|
| E1 容器隔离未验 | 环境 | parity 的 1 项未验。**细分**：资源约束层（进程数 / 内存 / 超时整组杀）由 Windows Job Object 实现并有 6 条用例，全绿；缺的只是**容器级的文件系统与网络命名空间**。`isolation` 字段如实写 `job-object`，不冒充 docker（`win_job.py` 开头就写明了这条纪律） |
| 真模型路径未验 | 环境（需 key） | `chat` 只验了规则模式；协议由假端点 11 条覆盖 |
| 流式只验了分帧 | 环境（需 key） | 6 条用例覆盖 SSE 拼接/坏包/回调；**真端点下的首包延迟、断流重连、usage 分片未验** |
| MCP 只实现最小三段 | 设计 | `initialize`/`tools/list`/`tools/call`；resources/prompts/sampling 未做 |
| 集成约 78 分钟 | 工程 | 贴着 CI 单 job 上限，见 §10.4 |
| ~~浏览器进程残留~~ | **已结案（B506/B507）** | 曾连续四轮误判。真正原因是：清空前的 baseline 里混着上一次被 kill 的跑留下的孤儿。带 PID 哨兵重跑：453 项 0 次命中、跑完实况 0 个进程——**正常路径不产生遗留** |
| 23 条「死代码」 | **已分诊（B510–B518）** | 六种性质：半实现 7（预算记账四项从未调用，报告恒 0）、无淘汰 2（缓存）、零缺口 4（守卫判据在别处）、判据在别处 3、真未展示 2、类型 4、调度 1。**不是一份可删清单**；详见 `H33 §13` |
| bench 语料只覆盖部分 oracle | 设计 | 判定能力看 `tests/cases`，那里一个不缺 |
| 真实目标召回约 0.33 | 环境 | 结构推断已接线（靶场实测 1→3 个可跑 oracle），**外部指标未复测**——需要能访问外部目标的环境 |
| 资产发现 / 扫描、IDE 插件 | 策略性不做 | `D-H-03` 决定；离线包要零依赖 |

---

## 4. 还剩什么

### 需要你拍（14 条，全部在 `plan-hos-ares/H33-待裁规格事项.md`）

拍完按 `H33 §12` 的现成顺序执行，**7–8 批**。其中 4 条各只需 1 行：

- §4 限流默认开关
- §10.2 安装权限（`admin` → `lowest`）
- §10.9 lab 补根路由
- §10.5 `make install-dev`

### 需要环境

- **E1**：装 docker 或 podman（或 WSL 发行版）→ 可把 parity 的 22 项做满
- **真模型**：起一个 OpenAI 兼容端点 + 给 key → 可验 `chat` 的 live 路径

### 我能自己做的

生产性工作已清零。剩下只有低优先级项（浏览器残留定位需重跑 75 分钟）。

---

## 5. 这份账怎么自证

```bash
python scripts/verify.py --quick-tests     # 闸门 + 单测
pytest tests/routes tests/lab tests/fixtures -q
hos-ares parity                            # 5 面 × 22 项
hos-ares doctor                            # 25 项环境自检
hos-ares coverage                          # 判定矩阵
```

账本（498 批，append-only，`0 problems`）：`.hos-ares/batches.jsonl`，用
`python scripts/lint/ledger_check.py` 校验。

历史遗留项归口：`plan-hos-ares/H31-未结项归口.md`（105 条，逐条已分类）。
