# H10 · PR 与文件管理规范

> 目标两个：**任一批次能单独回滚**、**任一时刻能说清"这份文件是谁的"**。
> 本卷 25 批、跨 5 个面，文件会散在两个包 + 至少 6 个新目录里——不管住，半年后没人敢动。

---

## 1. 包与目录边界（谁在哪写）

```
HOS-ARES/
├─ plan-veripen/                ★ 冻结（plan-v3.6）：判定内核规格 + B1–B34 账本
│                                 只许 B44/B59 动两处：16 §4.1 断言行、check-plan.cjs 阈值
├─ plan-hos-ares/               ★ 本包（plan-hos-v1.0）：总规划 H00–H10 + 自检
├─ plan-veripen-ui/             ✗ 已撤销（内容并入 H06/H07，目录不保留）
├─ veripen_core/sandbox/        ☆ 新（B45）：runner / backend / policy
├─ veripen_core/render/         ☆ 新（B46）：浏览器执行体（归一化 + 证据）
├─ veripen_core/blackboard.py   ← B51/B57 扩展（不是重写）
├─ veripen_core/context.py      ← B52 扩展
├─ veripen_core/tools/surface.py ☆ 新（B47）：唯一工具面
├─ veripen_core/agents/orchestrator.py ← B49 扩展（三段式 role 不改）
├─ playbooks/                   ☆ 新（B50）：三个 yaml 资产
├─ veripen_cli/tui.py           ☆ 新（B56）
├─ veripen_cli/cmd_console.py   ☆ 新（B37）
├─ web/loader.py                ☆ 新（B37）
├─ web/console.py               ← B35/B36/B40/B54
├─ web/static/index.html        ← B38–B42（单文件 ≤700 行）
├─ web/static/__baselines__/    ☆ 新：文本快照基线
├─ scripts/ui/                  ☆ 新：contrast/a11y 检查
├─ tests/unit/test_ui_*.py      ☆ 新
├─ tests/integration/test_{sandbox_isolation,browser_render}.py ☆ 新
├─ Makefile                     追加目标，不改已有目标语义
└─ .veripen/batches.jsonl       append-only，每批一行
```

**冻结规则的四种例外**（其余一律不许动 `plan-veripen/`）：

| 例外 | 谁批准 | 附带要求 |
|---|---|---|
| B44/B59 改 `16 §4.1` + `check-plan.cjs` 阈值 | operator 在 PR 确认 | 重生成旧包 `MANIFEST.md`；`check-plan.cjs` 与 `_verify-manifest.cjs` 双绿 |
| 判定语义确有 bug | 先写"判据为何不合理" | 走 D2：改完重跑全部历史 |
| 旧包机械门误报 | 单独 PR | 不许在功能 PR 里顺手改门 |
| 规格与实现冲突 | 先改本包 | 判定/安全类冲突才许改旧包 |

---

## 2. 分支与提交

| 项 | 约定 |
|---|---|
| 基线 | `main`（`origin/main` 已同步；若再次领先，先 `git push origin main`） |
| 分支名 | `b<NN>/<slug>`：`b45/sandbox-isolation`、`b50/playbooks`；方案包用 `plan/hos-v1.0` |
| 一分支一批 | 禁止一分支两批（`B45+B46` 也要拆） |
| 提交前缀 | `feat(<face>):` / `fix(<face>):` / `test(<face>):` / `docs(plan):` / `chore(<face>):`，`<face>` ∈ `ui|exec|platform|workbench|kernel` |
| 提交正文 | 必带判据结论（通过/未通过/未验）+ 判据命令 |
| 禁止 | `--no-verify`、直推 main、force push 已评审分支 |

---

## 3. PR 模板（每批照填）

```markdown
## 本批判据
- [ ] 涉及断言（A13–A20 中具体一条）
- [ ] 反例注入已实测（贴红输出）
- [ ] 现有 test_console.py 23 例未改断言

## 证据
- 命令与输出（最后 10 行）
- 反例命令与输出
- 快照 diff（有视觉改动时，`-/+` 各 ≤20 行）

## 账本
- [ ] `.veripen/batches.jsonl` 已 append（dod/evidence/open/notes）

## 回滚
- 单提交 revert / 需连带回滚项

## 负结论（D4）
- 未验的（含环境缺失 SKIP）、跳过的、没做的
```

---

## 4. 合并前检查单

```bash
node plan-hos-ares/check-plan.cjs                # 本包结构 + 能力矩阵/批次一致性 + 锚点 + MANIFEST
python -m pytest tests/unit/test_console.py -q   # 23 例基线
python -m pytest tests/unit/test_ui_*.py -q      # 本批新增
node plan-veripen/check-plan.cjs                 # 旧包机械门仍绿（B44/B59 前应始终绿）
curl -s -o /dev/null -w "%{http_code}\n" -X POST http://127.0.0.1:8099/ui/findings   # 405
```

`make ui-serve` + `H08 §5` 的 10 分钟人工脚本；PR 贴"走到第几步、哪步异常"。

---

## 5. 文件落位与体积

| 产物 | 落位 | 入库 | 上限 |
|---|---|---|---|
| 方案（本包） | `plan-hos-ares/*.md` | ✅ | 单篇 ≤ 40KB |
| Playbook | `playbooks/*.yaml` | ✅ | 20KB/个 |
| 快照基线 | `web/static/__baselines__/*.txt` | ✅ | 200KB/个 |
| 检查脚本 | `scripts/ui/*.py` | ✅ | 无第三方依赖 |
| 容器镜像定义 | `sandbox/`（Dockerfile 等） | ✅ | **不含地址** |
| 本批 raw 输出 | `out/<batch>/*` | ❌（gitignore） | — |
| 截图（人工） | 贴 PR | ❌ | — |
| 账本 | `.veripen/batches.jsonl` | ✅ append-only | — |

---

## 6. 账本协议

| 规则 | 内容 |
|---|---|
| 只增不改 | 修正用新行（`"state":"done(fixup)"`） |
| 一行一批 | `batch` 唯一，重复即事故 |
| 必填 | `batch/state/started/ended/scope/files/dod/evidence/open/notes` |
| `dod` 写法 | 逐条判据 + `✅/❌/未验`，不写"基本完成" |
| `open` | 留给哪批 |
| `notes` | 负结论、口径变化、环境缺失 |

---

## 7. 回滚矩阵

| 批次 | 回滚 | 连带 |
|---|---|---|
| B35/B36/B40 | revert 单提交 | 前端兼容未知字段，不必回滚前端 |
| B37 | revert | 薄壳保留旧路径一版 |
| B38–B42 | revert | 基线随提交回滚 |
| B43/B48 | revert（纯文档） | 无 |
| B44 | revert | **必须同时回滚断言表 4 行与阈值 16→12**，重生成旧包 MANIFEST |
| B45 | revert | `act.*` 回旧行为，同时把 `exec-isolation` 标红（不许留"绿但无用"的断言） |
| B46 | 执行体可摘除 | profile 关闭即可，不动判定链 |
| B47 | revert | 旧注册表保留一版 |
| B49 | 并行开关默认关 | revert 回单任务 |
| B51/B52 | revert | 旧行为回落（只记不消解 / 裁剪不变） |
| B57 | 列可空保留 | 旧代码忽略新列 |
| B54 | revert | approvals 页消失，CLI 审批仍可用 |
| B59 | revert | **必须连带回滚断言表 4 行与阈值 20→16** |

**跨批回滚禁令**：B39 出问题不许回滚 B35（不同语义层），否则把"不说谎"一起扔了。

---

## 8. 编号字典（防撞号）

| 类型 | 已用 | 本包新增 | 规则 |
|---|---|---|---|
| 批次 | B1–B34 | **B35–B59** | 全局唯一，续最大号 |
| 断言 | A1–A12 | **A13–A20** | 对外只讲这 8 条 |
| 红线 | D1–D4 | **H1–H4** | 面级红线 |
| 决策 | D-01–D-10 | **D-H-01–D-H-08** | 前缀区分 |
| 风险 | R1–R6c | **R-H1–R-H10** | 同上 |
| 文档 | 00–40, 99 | **H00–H10** | `grep -l 'H0'` 即分流 |

---

## 9. 一次完整流程

```bash
# 0 基线同步（仅当本地领先）
git push origin main

# 1 开分支
git switch -c b45/sandbox-isolation main

# 2 做活 + 跑门
python -m pytest tests/integration/test_sandbox_isolation.py -q
make exec-isolation
node plan-hos-ares/check-plan.cjs

# 3 账本 append → 提交
git add veripen_core/sandbox tests/integration/test_sandbox_isolation.py Makefile .veripen/batches.jsonl
git commit -m "feat(exec): 容器隔离执行" -m "判据A-E 见 H07 B45；podman 未测记未验"

# 4 推 + PR
git push -u origin b45/sandbox-isolation
gh pr create --base main --title "b45: 容器隔离执行" --body-file .github/pull_request_template_exec.md

# 5 合并后
git switch main && git pull --ff-only
```
