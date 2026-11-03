# 34 · Harness 操作台规格（v3.3 新增）

> `22` 写的是**报表**（能签字），本篇写的是**操作台**（能看着它干活、能中途接管）。
> 两者不是替代关系：`22` 是事后，本篇是事中。
>
> **为什么现在必须有**：`35` 引入了自由动作（`act.*`），
> 一旦 agent 能自己跑命令，"人看不见它在干什么"就从体验问题升级成**安全问题**。

---

## 0. 三档，按"离得多近"分

| 档 | 形态 | 给谁 | 能做什么 | 不能做什么 |
|---|---|---|---|---|
| **L0 TUI** | 单终端 `make tui TASK=x` | 工程师现场 | 看当前步/预算/待审批；`p` 暂停、`a` 批准、`r` 重发现、`q` 退出 | 不能改 verdict |
| **L1 简化 Web**（主推） | 单页三栏，SSE 实时 | 交付方 + 甲方安全团队 | 看会话流 / 黑板 / 线索账本；暂停恢复；审批；注入 selector 提示 | **不能改 verdict、不能复制地址、不能导出 route 模板** |
| **L2 报表**（已有，`22`） | 8 页只读 | 甲方合规/管理层 | 审报告、点 capsule、看覆盖 | — |

**一句话分工**：L0 给"正在按键盘的人"，L1 给"会议室投影"，L2 给"归档"。

---

## 1. 页面结构（L1 单页三栏 + 两常驻）

```
┌──────────────────────────────────────────────────────────────────────────────┐
│ web-01  RUNNING   ███████▁▁▁ 96/120   tok 210k/400k   线索 12/19 已闭环  ⏸ 暂停 │
│ routeset 6d20… · contract 12 · observed 4 · unresolved 2   │ ⚠ 1 待审批          │
├──────────┬─────────────────────────────────────┬───────────────────────────────┤
│ 会话 (3) │  动作流（每步一条）                  │  右栏（三 Tab）                │
│          │                                     │  [黑板] [线索] [覆盖]          │
│ ● web-01 │ [12] verify  idor_cross_account     │  ▸ 黑板事实 (47)               │
│  RUNNING │      依据: L0 object_read ×3         │   ● server=nginx/1.24  ev_3f   │
│          │      ┌ route://contract/7f3a… ────┐ │   ⚠ disputed  server           │
│ ○ web-02 │      │ params {selector:object_read}│ │   ◌ stale (ttl 到)             │
│  DONE    │      │ ▸ 原始响应 (1.2KB)  [回读]   │ │  ▸ 线索 (12)                   │
│          │      └─────────────────────────────┘ │   ● L-07 act.http_fuzz         │
│ ⏸ api-03 │ [13] ⭐VERIFIED  证据 contract · replay OK│     → 可疑差分  verifiable_by: │
│  PAUSED  │      [① capsule] [▶ replay]          │          idor,sqli_bool  pending│
│          │ [14] act  exec_tool nmap top_ports   │   ○ L-05 refuted (sqli_bool)   │
│          │      ⟨沙箱 sandbox-2⟩  exit=0 18s    │   ◌ L-09 unverifiable          │
│          │      ▸ 输出 4.2KB（尾部含开放端口） │       缺 file_read 面          │
│          │      ⇒ 产出 lead L-07                │                                │
│          │ [15] ⟳ 强制闭环 → 转向 L-07          │  ▸ 未覆盖 (2)                  │
│          │                                     │   file_read / graphql_endpoint │
├──────────┴─────────────────────────────────────┴───────────────────────────────┤
│ ⚠ 审批 reversible_write · mass_assignment · 等待 secops_lead   [批准] [拒绝]    │
└──────────────────────────────────────────────────────────────────────────────┘
```

**九个部件，缺一个都不算操作台**：

| # | 部件 | 数据来源 | 缺了会怎样 |
|---|---|---|---|
| 1 | 预算条 + 线索闭环进度 | `budget_json` + leads | 不知道还剩多少、不知道能不能收工 |
| 2 | 暂停 / 恢复 / 注入提示 | `PAUSED` + `task.pause/resume/inject_hint` | 只能干等或 kill |
| 3 | 动作流（含"依据"） | `task_events` + planner reason | 看不出它为什么这么干 |
| 4 | 动作卡片（折叠 / 回读） | `persisted_ref` | 只能看摘要，查不了原文 |
| 5 | **沙箱执行卡**（`act.*` 专用） | act trace | **看不见它跑了什么命令 = 安全事故** |
| 6 | 黑板面板（冲突/stale 可见） | `blackboard_facts` | 黑板只是表，不是资产 |
| 7 | **线索账本面板** | `leads` | "能打同时能证明"看不见 |
| 8 | 审批卡（阻塞式置顶） | `approvals` | 任务卡住没人知道 |
| 9 | SSE 实时流 | 新端点 | 只能刷新页面 |

---

## 2. SSE 事件协议（一个端点，五类事件）

`GET /tasks/{id}/stream`（`24` 新增 op_id `task.stream`）

```jsonc
// 1 动作
{"t":"step","seq":14,"kind":"act|verify|plan|rediscovery","oracle_id":null,
 "action":"exec_tool","route_ref":"route://contract/7f3a…","ok":true,
 "elapsed_ms":18400,"truncated":true,"persisted_ref":"out/act/9c1f.txt",
 "summary":"nmap top_ports → 3 open"}

// 2 预算
{"t":"budget","steps":{"used":14,"max":120},"tokens":{"used":210000,"max":400000},
 "leads":{"pending":1,"verified":2,"refuted":1,"unverifiable":1}}

// 3 黑板
{"t":"fact","id":"f_88","subject":"web-01","predicate":"server","value":"nginx/1.24",
 "from_target":1,"evidence_id":"ev_3f","disputed":false}

// 4 线索
{"t":"lead","lead_id":"L-07","state":"pending","source":"act.http_fuzz",
 "verifiable_by":["idor_cross_account","sqli_bool_diff"],"confidence_hint":0.4}

// 5 审批
{"t":"approval","approval_id":"ap_12","action_class":"reversible_write",
 "reason":"mass_assignment","state":"PENDING","approver_role":"secops_lead"}
```

**三条约束**：
1. 事件里**不含模板正文、不含 host/IP**（只有 `route_ref`）。
2. `act.*` 事件必须带 `sandbox_id`，否则前端不该渲染执行卡。
3. SSE 只推**新增**，不推历史；历史走 `GET /tasks/{id}/events?after=seq`。

---

## 3. 控制边界（可管什么、永不可管什么）

| 操作 | L0 | L1 | 为什么 |
|---|---|---|---|
| 暂停 / 恢复 | ✅ | ✅ | 控制"做不做" |
| 提预算（`budget_delta`） | ✅ | ✅ | 控制"做多少" |
| 审批（批准/拒绝） | ✅ | ✅ | 本来就是人决定的 |
| 注入 selector 提示 | ❌ | ✅ | 影响"打哪类面"，但**不许给 ref、不许给地址** |
| 重发现（触发 `rediscover_routes`） | ✅ | ✅ | 受 `max_rediscovery` 配额 |
| **改 verdict** | ❌ | ❌ | 纯函数（`17 I4`） |
| **改 finding 严重度** | ❌ | ❌ | 规则算出 |
| **导出 route 模板** | ❌ | ❌ | `27 N3`（导出需 `routes:read` + 审批） |
| **复制地址** | ❌ | ❌ | 前端 `user-select:none` + 无剪贴板 API |

**"注入 selector 提示"的输入约束**（容易出错的地方）：
只接受 `18 §6` 的 18 个标签之一 + 一句理由，**不接受自由文本**。
否则它就是"人给模型下指令"的后门，会破坏 `08 §7` 的隔离。

---

## 4. 技术选型（为什么不上 React）

| 项 | 选择 | 理由 |
|---|---|---|
| 后端 | FastAPI + SSE（`sse-starlette`） | 已有 FastAPI；SSE 比 WS 简单，够用 |
| 前端 | **原生 JS + 单页**（约 1,500–2,000 行） | 价值在数据组织；表 + 流 + 折叠，不需要框架 |
| 样式 | 一份 CSS，深色，等宽字体用于 trace | — |
| 状态 | 无（每次事件重渲染受影响的分区） | 无框架 = 无状态管理债 |

**唯一的前端复杂度是"动作流的虚拟滚动"**（120 步 × 每步可能百行输出）。
做法：只渲染可见的 20 条 + 折叠项，展开时按 `persisted_ref` 分页拉。

---

## 5. 验收（B12.8 收尾）

| # | 断言 | 命令 |
|---|---|---|
| 1 | 九部件齐全（缺一即不合格） | 人工清单 + 截图存档 |
| 2 | SSE 五类事件都能推 | `curl -N /tasks/x/stream` 看到五种 `t` |
| 3 | **页面无任何地址** | `make lint-ui`（DOM 快照正则，`.invalid` 白名单） |
| 4 | 越界次数非 0 时页头变红 | 造一条 scope 拒绝事件 |
| 5 | `act.*` 卡片显示沙箱与退出码 | 跑一次 `exec_tool` |
| 6 | 待审批时任务卡在 `PAUSED`，批准后续跑 | 端到端 |
| 7 | 复制地址行为被阻止 | 手测 + 无 `navigator.clipboard` 调用 |
| 8 | L0 TUI 在无浏览器环境可用 | `make tui` 在纯终端跑通（含暂停/批准） |

---

## 6. 明确不做

多用户注册 / RBAC（`99 P-15`）、在线编辑 finding（`17 I8`）、实时协作编辑、移动端适配。
