# H02 · 执行面规格（对标 strix）

> strix 的核心不是"会用工具"，是**"在隔离环境里真的动手"**。
> 我们现在能发 HTTP、能收 DNS 回调，但**所有动作都跑在宿主机同一个 Python 进程里**——
> 这不是执行面，这是"一个会发包的库"。
>
> 本卷三件事：**隔离（E1）、渲染（E2）、工具面统一（E3）**；外加两个门（E4 发现、E5 并行）。

---

## 1. 现状取证（不是感觉）

| 事实 | 锚点 | 后果 |
|---|---|---|
| 执行体只有 `subprocess`，无容器 | `veripen_core/workbench/executor.py:57`（`make_subprocess_executor`） | 工具的输出、临时文件、环境变量全部落在宿主机 |
| 工具面断裂 | agent 只见 7 个工具（`veripen_core/tools/registry.py:51` 起：`list_routes` / `run_oracle` / `list_assets` / `rediscover` / `record_note` / `request_approval` / `finish`），而 fs/shell 在 `workbench/` 里另开一套 | agent 拿不到工作台能力，工作台也不受 agent 的预算/审计约束 |
| 无浏览器 | 全仓 grep 无 `playwright|selenium` | 任何需要 JS 渲染的面（SPA、前端路由、DOM XSS）都测不了 |
| compose 只有 api 一个服务 | `docker-compose.yml:1` | 有 compose 文件 ≠ 有隔离执行；它只是编排 API 进程 |

---

## 2. E1 · 隔离执行（B45，3 人天）

> **执行记录（2026-11-03）**：`veripen_core/sandbox/` 落地（接口 / docker 后端 / 出网白名单策略 /
> 镜像无地址扫描 / 加固 argv / BLOCKED 语义），策略与形状全部真验；**真隔离未验**——
> 本机无 docker/podman（实测 `command not found`），A17 的宿主零副作用项记 `未验`。
> 该批在账本中记 `scaffolded`，W2 的门因此未过。

### 2.1 接口（先定形状，再选运行时）

```python
class SandboxRunner(Protocol):
    def run(self, action: Action, *, budget: Budget, bindings: RouteSet) -> ExecResult: ...
    # ExecResult: exit_code, stdout, stderr, artifacts[path], net_log[redacted], outcome
```

| 规则 | 内容 |
|---|---|
| **唯一出口** | 所有 `act.*` 只能经过 `SandboxRunner`；`executor.py` 的 subprocess 路径保留但**只用于 workbench 本地文件操作** |
| **无运行时 = BLOCKED** | docker/podman 都不可用时，返回 `outcome=BLOCKED`（`runtime_unavailable`），**不许静默回退到宿主机执行** |
| **每任务一容器** | 容器生命周期 = 任务生命周期；任务结束（含失败）必须销毁，残留容器视为事故 |
| **地址只能运行时注入** | 容器要访问目标，靠 `RouteSet` 解析结果注入为容器内变量；**镜像、compose、Dockerfile 里不许出现地址**（H2） |
| **出网白名单** | 只放行"已解析绑定所在 host + OOB 回调域名"，其余一律拒；拒绝事件进 `bounds` 页 |
| **资源上限** | CPU ≤ 1、内存 ≤ 1GB、pids ≤ 256、单动作 ≤ 120s、单任务累计 ≤ 30min（沿用五预算） |
| **产物只出目录** | 容器内 `/out` 挂到任务目录；其余路径不挂载、不复制 |

### 2.2 判据（A17，必须能红）

| 判据 | 怎么验 | 注入什么会红 |
|---|---|---|
| 宿主机零副作用 | 在容器内 `touch /tmp/x`、写 `/etc/hosts`，断言宿主对应路径不存在 | 把挂载改成 `-v /:/host` → 红 |
| 无残留 | 任务结束后 `docker ps -a` 无本任务容器 | 去掉 `--rm`/清理逻辑 → 红 |
| 无运行时则 BLOCKED | 隐藏 `docker` 与 `podman` → `outcome=BLOCKED`，且**零请求发出** | 改成回退 subprocess → 红 |
| 出网白名单 | 容器内访问未绑定 host → 连接被拒 + `bounds` 记一条 | 放开 `--network host` → 红 |
| 地址不在镜像 | `grep -rE 'https?://'` 扫 Dockerfile/compose/镜像内脚本 → 0（allowlist 除外） | 塞一个字面量 → 红 |

---

## 3. E2 · 浏览器渲染执行体（B46，4 人天）

### 3.1 用途边界（先划死，防止变成"能爬一切"）

| 允许 | 不允许 |
|---|---|
| 目标页面的 DOM 快照与网络请求记录（用于判定"前端渲染出的面"） | 带走凭据、登录态持久化 |
| 截图作为证据附件（不进 verdict） | 用截图"看起来像 XSS"当结论 |
| 与 oracle 配对（baseline/probe/control 各一次渲染） | 在页面里执行任意脚本（`page.evaluate` 只允许取样模板） |

### 3.2 契约（2026-11-04 B46 落地）

> `veripen_core/render/`：`dom.outline()` 归一化（丢易变属性、排属性序、折叠空白、跳过 script/style 正文）、
> `dom_sha256()` 指纹、`redact_url()` 只留 `ref:<8位>` 与查询参数名；
> `BrowserRenderer.render()` 每次 **new_context**（不共享 cookie）、超时归一为 `blocked/render_timeout`、
> 截图哈希入结果。**`isolation=host` 显式写在结果里**：本机无容器，容器内渲染未验。
> `make browser-render` 真跑 Chromium：同输入同哈希、两次渲染两个 context。

```python
render(binding: RouteBinding, *, wait: "networkidle|domcontentloaded", timeout_s: int) -> RenderResult
# RenderResult: dom_outline(str, 归一化), net_log(list, url 脱敏为 ref), screenshot_digest, outcome
```

| 规则 | 内容 |
|---|---|
| 确定性 | 同输入两次渲染，`dom_outline` 的 sha256 必须相同（截图允许抖动，DOM 不允许） |
| 归一化 | 去时间戳、去随机 id、属性排序、空白折叠——归一化规则写死在代码里并单测 |
| 隔离 | 浏览器跑在 E1 的容器里；不共享 cookie jar；每个动作新 context |
| 证据 | `dom_outline` + 脱敏 `net_log` 进 capsule；截图只作附件（digest 入 capsule） |
| 失败语义 | 渲染超时/崩溃 → `BLOCKED(render_timeout)`，**不得**退化成"没发现" |

> **执行记录（2026-11-04，B58）**：离线包已支持双形态 `--profile light|browser`；
> light 不含浏览器载荷，browser 只放 `browser/README.txt`（**不塞占位二进制**）。
> `doctor` 的 deps 一行同时报 `tui:` / `sandbox:` / `browser:` 三项，缺哪项一眼可见。
> **A3 仍未闭合**：本机无 docker，`images/` 为空，`verify` 会如实报出，不静默通过。

**离线包代价（要写进对外材料）**：Playwright + Chromium ≈ 300MB，离线包体积从 ~40MB 涨到 ~340MB；
`make package-offline` 必须能不带浏览器产出"轻量包"，由 profile 决定装不装（`D-H-02`）。

---

## 4. E3 · 工具面统一（B47，2 人天）

### 4.1 现状的断裂（这就是"像组件不像产品"的具体形态）

```
agent 的 7 个工具           workbench 的另一套
├ list_routes               ├ fs.read / fs.write / fs.edit
├ run_oracle                ├ shell.run（五道闸）
├ list_assets               └ plan / todo
├ rediscover
├ record_note
├ request_approval
└ finish
        ↑ 两套各自有自己的校验、预算、审计 —— 同一个动作，两条路径，两份记录
```

### 4.2 目标形状（2026-11-03 B47 已落地）

> `veripen_core/tools/surface.py`：`AGENT_IMPACT`(7) + `fs.read/write/edit` + `shell.run` + `browser.render`；
> 注册去重、catalog 带 impact、exec 需显式 `approved=True`、每次调用进 audit。
> 模型白名单与 `tool_schemas()` 均从 surface 派生——**清单只有一个来源**。
> `browser.render` 与 `shell.run` 的实现注入点仍空（前者 blocked，后者依赖 shell 五道闸）。

```
        ┌──────────── 单一 ToolSurface ────────────┐
        │ 名称唯一 · schema 唯一 · 准入唯一 · 审计唯一 │
        └───────┬──────────────┬──────────────────┘
        verify.*│       act.*  │  workbench.*
        oracle  │  sandbox/HTTP│  fs/shell/plan
```

| 规则 | 内容 |
|---|---|
| 名称唯一 | 清单只有一个来源（注册表）；重复注册 → 启动即失败 |
| 分级准入 | schema 上带 `impact: read|write|exec`，`exec` 必须过审批门 |
| 预算统一 | 所有工具共用五预算（步数/请求/tokens/时长/工具调用数） |
| 审计统一 | 每次调用写 `task_events`，含 `tool` / `impact` / `allowed_by` |
| 判定隔离 | `workbench.*` 与 `act.*` 在类型上拿不到 verdict（沿用 `I12`） |

**判据**：`make tool-surface` —— ① 清单去重 ② agent 能调到 `fs.read` 与 `shell.run` ③ 权限分级可见
④ 同一动作两条路径的旧代码被删（不是并存）。

---

## 5. E4 · 资产发现（B48，3 人天，**决策门，默认不做**）

strix 会扫网段/找资产，我们 `16 §7` 明确"维持不做"。
**B48 只产出结论，不产出代码**：

| 要评估的 | 内容 |
|---|---|
| 做它要改什么 | `scope` 四处校验要新增"发现态"；`RouteBinding` 要允许"未验证来源"；审批门要新增 `scope_expand` 的高频路径 |
| 风险 | 一旦允许发现，`H2 地址不入库` 会出现第一个例外（发现结果本身就是地址集合） |
| 结论二选一 | ① 不做（维持现状，对外口径不变）② 做，附最小可验证边界（例：只扫同一 CIDR 且需审批） |

**判据**：结论写进账本；若给"做"，必须附"发现结果如何不进 verdict、如何脱敏、如何过期"三条设计，否则视为未评估。

---

## 6. E5 · 并行执行（B49，4 人天）

| 规则 | 内容 |
|---|---|
| 并行单位 | **任务**，不是动作；同任务内仍单 agent 串行（保护可 replay） |
| 上限 | 3（与 worker 数一致，`D-H-05`） |
| 隔离 | 黑板/leads/capsule 全部按 `task_id` 过滤；跨任务读=事故 |
| 调度 | 阶段级推进（`workflow.py` 已有 DAG 雏形）→ 补"阶段失败回退到上一阶段"与"阶段级预算" |
| 判据 | 三任务并发跑同一批用例，findings 与串行逐条相同；交错顺序不影响结果（`findings_sha` 相同） |

---

## 7. 这一卷做完，对外能讲什么（也只能讲这些）

| 能讲 | 命令 |
|---|---|
| 动作在隔离环境里执行，宿主机无副作用 | `make exec-isolation` |
| 能测需要 JS 渲染的面，且渲染可复现 | `make browser-render` |
| agent 与工作台是同一套工具面，权限与审计统一 | `make tool-surface` |
| 三任务并行结果与串行一致 | `make parallel-eq` |

**不能讲**："能打一切协议"（仍只有 HTTP/DNS/TLS + 浏览器）、"全自动资产发现"（E4 默认不做）、
"容器逃逸防护"（我们只保证容器内外隔离，不保证容器本身不可逃逸）。
