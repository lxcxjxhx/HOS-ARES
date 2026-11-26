# HOS-ARES 状态总账

> **最后更新**：2026-11-26（账本 543 批）。这份文件不带日期后缀——它随仓库一起演进，历史快照看 git log 或 `docs/delivery/final-acceptance-*.md`。

一句话：**判定内核、三支柱、交付形态、界面与成片都已闭合且有证据；22 项对标 ✅20 / ◐1 / ❌0，剩下的一项要能访问外部目标。**

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
| 渗透 | `hos-ares run` | 契约 → oracle → capsule → 报告；**真 socket 端到端**（B540） |
| 发现 | `hos-ares discover` | 无契约从首页攒面（不产判定） |
| 平台 | `hos-ares platform` | 多资产 + 审批阻断（退出码 3）+ 汇总报告；真 socket（B540） |
| Agent | `hos-ares chat` | 计划 + 工具流 + diff（规则模式）；一句指令真改文件（B540） |
| └ codex 式能力 | 见下 | **一次多工具调用 · 多文件 patch · MCP client · 流式收包**，四项各有用例 |

### 交付形态

| 形态 | 状态 |
|---|---|
| console script | `hos-ares --version` → `0.1.0`（`--help` 全用连字符） |
| wheel | `pip wheel .` → 231 文件，含 `hos_ares.{bench,doctor,report}` 全部子包 |
| exe | `out/dist/HOS-ARES.exe` → 启动后 `/ui/app` 200 + `<title>` + `id="nav"` |
| 安装包 | `make installer-check` → 装得上 / 装了能打开 / 卸得干净（**B539 本地可复核**，约 80 秒，无需管理员） |
| 成片 | `out/demo/hos-ares-tour.mp4` 62.9s / 1280×720 / H.264 |

---

## 2. 验收证据（2026-11-26 实测，E1 已翻绿）

```
tests/unit                       2137 项   0 失败（1 skip：Windows 符号链接）
tests/integration                 含 sandbox 隔离 11 项，0 skip
verify                             12 项   全绿（含 mypy / 五道 lint / 账本 / 结构）
doctor                            沙箱执行体：wsl-podman（isolation: container）
hos-ares parity               22 项：green 22 / 未验 0 / red 0
```

一条命令复现闸门：

```bash
python scripts/verify.py --quick-tests    # 12 项（含单测）
```

**22/22 全绿**：E1 执行隔离首次验过。它不是"装了 docker"，而是
`select_executor()` 找到 **WSL 发行版里的 podman** 并真的起了一个容器：
容器内 `touch /tmp/…` 在宿主看不到、挂进来的只读宿主管写不动。

---

## 3. 已知边界（不隐瞒）

| 边界 | 性质 | 影响 |
|---|---|---|
| 执行体探活有两道判据 | 工程 | 选执行体时要问 `docker info` **并**看 `OSType`（B541/B543）。只问「进程在」会选出 Windows 容器模式的引擎，加固参数一条不被接受（`invalid option: read-only mode`）。两者任一不满足就继续往下选：WSL podman → Job Object → BLOCKED |
| ~~E1 容器隔离未验~~ | **已结案（B536/B541）** | 容器引擎在 **WSL 发行版里**（`wsl -d <distro> --exec podman`），`select_executor()` 新增这一档并实测通过：容器内 `touch /tmp` 宿主不可见、`--mount …,readonly` 写不动、超时在**容器内**触发（退出码 124）。`isolation` 如实写 `container`；Job Object 档仍写 `job-object`，两者不混。**B541**：`available()` 改为真探活（`docker info`）——CI 有 docker CLI 没守护进程，以前会被选中然后每条命令回 125 |
| ~~容器级**并行**未验~~ | **已结案（B537）** | `parallel.run_parallel_containers`：每任务一个容器，并发结果与串行**逐条相同**；容器之间不共享文件系统（A 写 `/tmp/handoff`，B `cat` 得到 `gone`）。`isolation` 只在真起容器时是 `container`；非容器档返回 `blocked`，不降级 |
| 真模型路径未验 | 环境（需 key） | `chat` 只验了规则模式；协议由假端点 11 条覆盖 |
| 流式只验了分帧 | 环境（需 key） | 6 条用例覆盖 SSE 拼接/坏包/回调；**真端点下的首包延迟、断流重连、usage 分片未验** |
| MCP 只实现最小三段 | 设计 | `initialize`/`tools/list`/`tools/call`；resources/prompts/sampling 未做 |
| 全量验收约 50–78 分钟 | 工程 | `verify --with-tests` 实测 50 分钟（B534）与 78 分钟（B459）两次；贴 CI 单 job 上限，见 §10.4 |
| ~~浏览器进程残留~~ | **已结案（B506/B507）** | 曾连续四轮误判。真正原因是：清空前的 baseline 里混着上一次被 kill 的跑留下的孤儿。带 PID 哨兵重跑：453 项 0 次命中、跑完实况 0 个进程——**正常路径不产生遗留** |
| 23 条「死代码」 | **已分诊（B510–B518）** | 六种性质：半实现 7（预算记账四项从未调用，报告恒 0）、无淘汰 2（缓存）、零缺口 4（守卫判据在别处）、判据在别处 3、真未展示 2、类型 4、调度 1。**不是一份可删清单**；详见 `H33 §13` |
| 契约与目标路径不匹配 | **行为已验（B540）** | 退出码 0、四份交付物都在、**一条证据都没有**（八条 oracle 全 `undetermined`，`by_outcome.reported == 0`）。这是"体面地承认没测出来"，不是端到端成功。要验契约路径需要一份与靶场配套的 OpenAPI（靶场目前没导出） |
| bench 语料只覆盖部分 oracle | 设计 | 判定能力看 `tests/cases`，那里一个不缺 |
| 真实目标召回约 0.33 | 环境 | 三因拆开看：**OOB 缺域名**（需公网可达域名）、**端点与 oracle 不匹配**（需浏览器渲染，已有）、**标签没被采纳**（B538 已补出口）。三因里两条**依赖外部目标**，所以这个数字本机复测不了；本机能复测的是靶场上的结构推断，两者不同量 |
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

- ~~**E1**：装 docker 或 podman（或 WSL 发行版）~~ → **已解决（B536）**：WSL 里的 podman 接进 `select_executor`，22 项做满
- ~~**E5**：容器级并行~~ → **已解决（B537）**：每任务一容器，并发与串行逐条相同
- **真模型**：起一个 OpenAI 兼容端点 + 给 key → 可验 `chat` 的 live 路径
- **真实目标召回**：三因里两条（OOB 域名、端点匹配）依赖能访问的外部件

### 我能自己做的

- **真模型路径**：本机 LiteLLM 已装但**没配置**（无 `~/.litellm/config.yaml`、无上游 key）。
  配置了就绪后可验 `chat` 的 live 与流式。
- 低优先级：浏览器残留定位（需重跑 75 分钟）

---

## 5. 这份账怎么自证

```bash
python scripts/verify.py --quick-tests     # 闸门 + 单测
pytest tests/routes tests/lab tests/fixtures -q
hos-ares parity                            # 5 面 × 22 项
hos-ares doctor                            # 25 项环境自检
hos-ares coverage                          # 判定矩阵
```

账本（append-only，`0 problems`）：`.hos-ares/batches.jsonl`，用
`python scripts/lint/ledger_check.py` 校验。

历史遗留项归口：`plan-hos-ares/H31-未结项归口.md`（105 条，逐条已分类）。
