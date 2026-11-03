# H04 · 工作台规格（对标 codex）

> codex 的价值不在"更聪明"，在**"能连续干几小时的活而不丢上下文"**。
> 我们已经有 fs / shell / plan / todo（B19 落地，`make workbench-smoke` 绿），
> 缺的是**入口（TUI）**与**记忆（跨任务）**——也就是"用得久"的那一半。

---

## 1. 现状取证

| 能力 | 现状 | 锚点 |
|---|---|---|
| 文件读写编辑 | ✅ 有作用域校验（`check_relative` 拒绝逃逸） | `veripen_core/workbench/fs.py:115`（`Workbench`） |
| 命令执行 | ✅ 五道闸 + 进程组回收 | `veripen_core/workbench/executor.py:57`、`:137`（`_kill_group`） |
| 计划与待办 | ✅ 有（plan 门生效，见 A12） | `veripen_core/workbench/plan.py` |
| 交互入口 | **❌ 无 TUI**（全仓无 `textual` / `rich` / `curses` 使用） | grep 实测 |
| 跨任务记忆 | **◐ 只有 `asset_facts`**（只增不改），无归档/检索/过期 | `veripen_core/blackboard.py:67` |
| 代码执行沙箱 | **◐** 宿主机 subprocess（同 `H02 §2.1` 的问题） | `executor.py:57` |

---

## 2. W1 · 已建成部分（不动，只登记）

`make workbench-smoke` 的判据已经锁住四件事：`fs.write` 真落盘、`shell.run` 真执行、
`plan` 门生效、五道闸正确。**本卷不重做这部分**，只做两件事：给它入口（B56）、给它记忆（B57）。

> 提醒：`fs.*` / `shell.run` 目前**不在 agent 的工具面上**（`H02 §4`），
> 也就是说"工作台"和"agent"是两个割裂的系统。这条由 **B47** 打通，本卷依赖它。

---

## 3. B56 · TUI（3 人天）

### 3.1 选型（不引第三方）

| 方案 | 结论 |
|---|---|
| `textual` / `rich` | ❌ 引入依赖，离线包与"内网可装"成本上升 |
| `curses`（stdlib） | ✅ 采用（Windows 下用 `windows-curses` 运行时不可用则降级纯行模式） |
| 纯行模式（无 curses） | ✅ 兜底：任何终端都能跑，功能是 TUI 的子集 |

`D-H-04` 默认"做 TUI，用 stdlib 的 curses"；若 Windows 环境不可用，**降级到纯行模式并把降级记进 `doctor`**，不许静默。

### 3.2 三块面板（够用即可，不做花活）

> **执行记录（2026-11-04）**：`veripen_cli/tui.py` 落地——`render_panels()` 纯函数出三块面板
> （任务区含五预算 used/limit、动作流最近 8 步、线索区 pending/已闭环），
> `run()` 用 stdlib `curses`；无 curses（本机 Windows 即如此）自动降级 `line_mode()`，
> `doctor` 的 deps 一行显示 `tui: line-mode`。TUI 全程只读，源码里不出现 verdict。

| 面板 | 内容 | 数据源 |
|---|---|---|
| 任务区 | 当前任务 id / 阶段 / 步数 / 五预算余量 | `task_events` + `budget` |
| 动作流 | 最近 N 步：动作、影响级、结果、是否审批 | `task_events` |
| 线索区 | pending leads 数 + 强制闭环提示 | `leads.py` |

### 3.3 判据（能红）

| 判据 | 注入什么会红 |
|---|---|
| 无第三方依赖 | `import textual/rich` 出现即红（lint） |
| 只读展示 | TUI 里任何"改判定/改发现"的入口 → 红 |
| 降级可见 | 无 curses 时自动行模式，且 `doctor` 显示 `tui: line-mode` |
| 不阻塞主循环 | TUI 卡住不影响任务推进（同一任务在 TUI 关闭后继续跑完） |

---

## 4. B57 · 跨任务记忆（2 人天）

### 4.1 语义先定死（否则会变成"AI 记仇"）

| 类别 | 定义 | 例子 | 生命周期 |
|---|---|---|---|
| **资产事实** | 目标本身的稳定属性，带出处 | "PHP 8.1 + nginx"、"登录在 `/login`（语义面）" | 长（可过期，默认 30 天） |
| **判定结论** | 已出的 verdict | "xss_reflected_ctx = VERIFIED" | **不跨任务复用**（必须重验） |
| **打法经验** | playbook 的成功率统计 | "idor-enum-first 在本资产上 4/5" | 中（跟 playbook 走） |

**硬规则（三条反滥用）**：

1. **结论不跨任务**：新任务必须重跑判定，记忆只用来"少走弯路"，不许用来"直接下结论"（H1）；
2. **记忆必须有出处**：每条事实带 `source_event_id`，无出处不写入；
3. **过期即失效**：超过 TTL 的事实不进注入，且标 `stale`（不是删除）。

### 4.2 实现

| 项 | 内容 |
|---|---|
| 存储 | 复用现有 `asset_facts` 表 + 新增 `ttl_s` / `last_seen` / `source_event_id` 三列（迁移脚本，向后兼容） |
| 检索 | 按 `(asset, kind, tag)` 查询，取最近且未过期的前 N 条 |
| 注入 | 只进 L1a（资产事实层），**不进判定输入**（`assert_no_leak` 的既有约束不变） |
| 判据 | ① 结论不跨任务：删掉记忆后同一用例 findings 不变 ② 过期事实不注入 ③ 无出处不写入 |

---

## 5. 与执行面的交界（别重复造）

| 项 | 归属 |
|---|---|
| 代码执行沙箱 | **B45**（容器隔离），本卷只消费 |
| 工具面统一 | **B47**（fs/shell 进 agent 工具表） |
| 长会话压缩 | **B52**（上下文压缩算法），本卷不重复实现 |

---

## 6. 工作台面做完，对外能讲什么

| 能讲 | 命令 |
|---|---|
| 有一个不依赖第三方库的终端界面 | `make tui-check` |
| agent 能读写文件、跑命令，且和工作台是同一套工具 | `make tool-surface` |
| 资产事实能跨任务复用，但结论必须重验 | `make memory-check` |

**不能讲**："IDE 插件"（没做）、"跨客户记忆共享"（禁止）、"记忆让模型更聪明"（我们只做事实沉淀）。
