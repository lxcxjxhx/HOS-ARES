"""一次性：回填 H01 的 §2 能力矩阵（B75）。

按行定位替换，不依赖整段字符串精确匹配——文件里的空格/分类数与记忆未必一致。
"""
from __future__ import annotations

from pathlib import Path

P = Path(__file__).resolve().parents[2] / "plan-hos-ares" / "H01-能力矩阵与对标口径.md"

TABLE = """| # | 能力域 | strix | codex | 驭智 | **我们（实测）** | 差距 | 批次 |
|---|---|---|---|---|---|---|---|
| **V1** | 判定可信度 | ❌ 模型判 | — | ◐ 未公开细节 | ✅ 纯函数 + 三条件 + control：`veripen_oracles/` 15 类 | 无（**领先**） | 保持 |
| **V2** | 证据与 replay | ◐ 有轨迹无哈希链 | — | ◐ 有过程审计 | ✅ capsule 哈希链 + 六态 replay：`veripen_core/evidence.py` | 无 | 保持 |
| **V3** | 地址治理 | ❌ 配置写地址 | — | ❌ 未见表述 | ✅ `RouteRef` 无 url/host 字段：`veripen_core/routes/types.py` | 无（**唯一**） | 保持 |
| **V4** | 发现与覆盖账本 | ◐ 会扫不会算漏了什么 | — | ◐ 报"已验证率" | ◐ 15 类矩阵 + 四态 outcome，真实召回 **0.33** | 召回低；负面证明不完整 | 未排 |
| **E1** | 隔离执行 | ✅ 容器 | ✅ 沙箱 | ✅ 容器 | **◐ 策略已验、真隔离未验**：`veripen_core/sandbox/runner.py`（无运行时 → `select_executor` 返回 None → BLOCKED） | 缺运行时，非设计 | B45 `scaffolded` |
| **E2** | 浏览器渲染 | ✅ | ◐ | ✅ | ✅ `veripen_core/render/browser.py`（真 Chromium 10 passed，DOM 哈希两次一致） | 容器内未验 | B46 |
| **E3** | 工具面统一 | ✅ | ✅ | ✅ | ✅ `veripen_core/tools/surface.py` + `registry.py`：agent 与工作台同一套 | 无 | B47 |
| **E4** | 资产发现/扫描 | ✅ | — | ✅ | **❌ 明确不做**（`D-H-03`） | 策略性缺失 | B48 门 |
| **E5** | 长周期与并行 | ✅ | ✅ | ✅ 多智能体 | ◐ `veripen_core/parallel.py#run_parallel`（进程内三任务并发，指纹与串行一致） | 容器级并行未验 | B49 |
| **P1** | 控制平面 | ◐ | ◐ | ✅ | ✅ `veripen_core/agents/orchestrator.py#orchestrate` + `veripen_cli/cmd_platform.py` | 无 | B49 / B68 |
| **P2** | 状态黑板 | ❌ | ◐ | ✅ | ✅ `blackboard.py#disputed` + 冲突消解进报告 | 无 | B51 |
| **P3** | 上下文压缩 | ◐ 裁剪 | ✅ 长会话 | ✅ 三层 | ✅ `compression.py#compress` + `briefing.py#build_l1a`（生产接线，降幅 ≥30%） | 无 | B52 / B62 |
| **P4** | Playbook 资产 | ◐ 内置套路 | — | ✅ 按 PTES | ✅ `playbooks/` 3 个 + 成功率实测 + 写进平台报告 | 无 | B50 / B68 |
| **P5** | 插件与能力发现 | ◐ | ✅ MCP | ✅ | ✅ `plugins/lifecycle.py`（装→用→升级回滚→卸，catalog 逐字复原）+ `knowledge_docs()` 进 L2 | 无 | B55 / B63 |
| **P6** | 审批与人机 | ◐ 可配 | ◐ | ✅ 阻断式 | ✅ `guard/approval.py#decide`（落库 + 事件，拒绝留痕）+ `ui.approvals` 唯一写入点 | 无 | B54 / B60 |
| **P7** | 报告与审计 | ✅ | — | ✅ | ✅ `veripen/report/audit.py#render_audit_section`（五表）+ 平台报告含审计节 | 无 | B53 / B68 |
| **W1** | 工作台基础 | ◐ | ✅ | ◐ | ✅ `veripen_core/workbench/fs.py#Workbench` + 五道闸 | 无 | 保持 |
| **W2** | 长会话/跨任务记忆 | ✅ | ✅ | ◐ 单轮 | ✅ `veripen_core/memory.py` + `asset_facts` + L1a 注入（结论不跨任务） | 无 | B57 / B62 |
| **W3** | TUI/IDE 接入 | — | ✅ | ◐ 控制台 | ✅ `veripen_cli/tui.py`（curses 三面板，无 curses 降级行模式，doctor 可见） | IDE 插件不做 | B56 |
| **G1** | 控制台 | ✅ Web UI | ✅ TUI+IDE | ✅ | ✅ `web/console.py` 10 页专属渲染器 + 巡检录屏（`B65`） | 无 | B35–B42 / B64 / B65 |
| **G2** | 交付与离线 | ◐ | ◐ | ✅ | ✅ 离线包 + `veripen_cli/cmd_doctor.py#check_sandbox` 等 19 项（缺条件记 skip 不记 fail） | docker 实跑未验 | B58 / B61 |
| **G3** | 断言与验收 | — | — | — | ✅ `veripen_cli/cmd_parity.py#main`（5 面 × 22 项）+ 断言表 20 条 + `make demo` | 无 | B44 / B59 / B70 |"""

BACKFILL = """## 2.1 回填记录（2026-11-05，B75）

`§5` 要求"批次完成后回填此表"。从 B35 到 B74 共 40 批落地后首次全表核对。

**核对方式**：对 22 项逐条 grep 代码锚点（文件存在 + 关键符号存在），
不采信账本自述。21/22 项锚点命中；G1 的 `renderAudit` 在
`web/static/index.html`（前端）而非 `web/console.py`，已更正。

**状态变化**：

| 项 | 立项时 | 现在 | 依据 |
|---|---|---|---|
| E2 浏览器 | ❌ 全仓无 playwright | ✅ | B46 执行体，容器外渲染 10 passed |
| E3 工具面 | ◐ 两套工具表 | ✅ | B47 统一到 ToolSurface |
| P1 控制面 | ◐ 无阶段级调度 | ✅ | B49 并行 + B68 平台编排 |
| P3 压缩 | ◐ 只有"丢"没有"压" | ✅ | B52 算法 + B62 生产接线 |
| P4 Playbook | ◐ 只有规格 | ✅ | B50 三个资产 + 成功率实测 |
| P5 插件 | ◐ 无端到端 | ✅ | B55 装→用→卸 catalog 复原 |
| P6 审批 | ✅ 但界面缺失 | ✅ | B54 界面 + B60 落库与事件 |
| W3 TUI | ❌ 无交互入口 | ✅ | B56 curses + 降级可见 |
| G1 控制台 | ◐ 6 页裸 JSON | ✅ | B35–B42 专属渲染器 + B64 audit 接源 |
| E1 隔离 | ❌ subprocess 直跑 | **◐** | B45 接口/策略/BLOCKED 真验，真隔离待运行时 |
| E5 并行 | ◐ 单任务 | **◐** | B49 进程内已验，容器级未验 |

**统计变化**：

| | 立项时 | 现在 |
|---|---|---|
| ✅ 有 | 5 | **18** |
| ◐ 部分 | 10 | **3**（V4 / E1 / E5） |
| ❌ 无 | 3（E1/E2/W3） | **1**（E4，策略不做） |

剩下的 3 个 ◐ 里，**两个（E1 / E5）同一个原因**：本机没有容器运行时。
第三个（V4 召回 0.33）属契约标签采纳率，`H09 §4` 列为未排批次。"""


def main() -> int:
    lines = P.read_text(encoding="utf-8").splitlines()
    start = next(i for i, l in enumerate(lines) if l.startswith("| # | 能力域 |"))
    end = start
    while end < len(lines) and lines[end].startswith("|"):
        end += 1
    new = lines[:start] + TABLE.splitlines() + [""] + BACKFILL.splitlines() + lines[end:]
    P.write_text("\n".join(new) + "\n", encoding="utf-8")
    print(f"replaced rows {start}..{end} ({end - start} lines) with {len(TABLE.splitlines()) + len(BACKFILL.splitlines())}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
