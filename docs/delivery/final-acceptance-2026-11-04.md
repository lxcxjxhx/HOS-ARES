# 最终验收（2026-11-04）

> 本文是 `plan-hos-ares` 四波 25 批的收官记录。
> 结论先行：**能验的都验了；唯一验不了的是容器真隔离（本机无 docker/podman），按 operator 决定接受为"未验"并结项。**

---

## 1. 一条命令看全局

```bash
python -m veripen_cli.cmd_parity
# 合计 22 项：green 20 · 未验 1（E1 容器） · 不做 1（E4 资产发现，D-H-03） · red 0
```

四态语义：`green` 本机真跑通 · `unverified` 缺条件（**不许写绿**） · `skipped` 决策不做 · `red` 失败（退出码非 0）。

---

## 2. 波次与批次

| 波 | 批次 | 状态 |
|---|---|---|
| W1 产品面 | B35–B44（10 批） | ✅ 全部 done，M1 达成 |
| W2 执行面 | B45 `scaffolded` · B46 ✅ · B47 ✅ · B49 ✅ · B48 决策门 | 4/5 落地；M2 **未达**（容器真隔离未验） |
| W3 平台面 | B50–B55（6 批） | ✅ 全部 done |
| W4 工作台/收口 | B56–B59（4 批） | ✅ 全部 done，M4 达成 |

账本：`.veripen/batches.jsonl` 104 行（B1–B59），`ledger-check` 0 问题。

---

## 3. 断言（20 条，`plan-veripen/16 §4.1` v3.9）

| 组 | 结果 |
|---|---|
| A1–A16 | 绿（A3 的 docker 实跑除外，见下） |
| A17 隔离可证 | **未验**：无 docker/podman；策略/加固/BLOCKED 已验 |
| A18 浏览器可证 | 绿（**宿主渲染**：同输入同哈希 / 每次新 context / 超时 BLOCKED）；容器内未验 |
| A19 平台可证 | 绿（playbook 实测率 + 黑板消解 + 压缩不改判定） |
| A20 三对标可证 | 绿（`make parity`，red 即非 0） |
| A3 内网能装 | **未验**：`images/` 需在有 docker 的构建机组包 |

**统计**：20 条全部有命令；绿 18（A1/A2/A4–A16/A19/A20 + A18 宿主）；未验 2（A3、A17）。

---

## 4. 结项时接受的三条"未验"

| # | 项 | 为什么接受 | 复核触发 |
|---|---|---|---|
| 1 | 容器真隔离（E1 / A17 / A3） | 本机无 docker/podman；策略、加固参数、BLOCKED 语义已真验 | 装上任一运行时后补跑 `make exec-isolation`，把 B45 翻 `done` |
| 2 | 容器级渲染与并行（A18 后半、E5 后半） | 已在宿主/进程内验完语义；容器化属于同一环境缺口 | 同 1 |
| 3 | 平台接线尾巴（压缩进 L1a、插件 knowledge 进 L2、审批事件进 task_events） | 各自批次已有判据与模块，接线属集成打磨 | 下一轮迭代首项 |

---

## 5. 期间修掉的真实缺陷（不是"完成度"）

| # | 缺陷 | 影响 |
|---|---|---|
| 1 | 控制台把 `BLOCKED` 条目以 `high` 排进「立即看」 | 判定域 D1 在展示层被违反 |
| 2 | `route_short` 切前 8 位得到 `route://` | 泄露 scheme 且无识别力 |
| 3 | `el()` 吞掉第一个表头 | 页面少一列 |
| 4 | 模块缓存旧 `router.js` 导致整页白屏 | 升级即挂 |
| 5 | 页内锚点 `#main` 被路由当成换页 | 跳转链接失效 |
| 6 | 同毫秒事实按 ULID 判新旧 | 间歇性排序错误 |
| 7 | 26 个 `make` 目标从未真正写入（链式 replace 静默失败） | 断言表的 ✅ 一度是假的 |

---

## 6. 复跑全部证据

```bash
pytest tests/unit -q                                  # 全绿（1 skipped）
pytest tests/integration/test_browser_render.py -q    # 10 passed（真 Chromium）
python -m veripen_cli.cmd_parity                      # 22 项 / red 0
(cd plan-veripen && node check-plan.cjs)              # v3.9 / 20 断言 / OK
(cd plan-veripen && node _verify-manifest.cjs)        # 54 files verified
node plan-hos-ares/check-plan.cjs                     # 22 项 / 25 批 / 44.5 人天 / OK
node scripts/plan/check-plans.cjs                     # 3 包 / 注册表一致
python scripts/lint/ledger_check.py                   # 104 行 / 0 问题
```

---

## 7. 对外只能这么讲

| 能讲 | 不能讲 |
|---|---|
| 判定不依赖模型（A8） | "模型能自主渗透"（B29 已证否） |
| 地址不存在于代码（A9） | "能打一切协议"（仅 HTTP/DNS/TLS + 宿主渲染） |
| 每条结论可 replay（A4） | "容器隔离已证"（未验） |
| 真实资产召回 0.33、误报 0（B30，未达标但原因清楚） | "召回达标" |
| 界面说真话：未验证不冒充高危（A13） | "控制台是产品"（B43 结论：停止） |

---

## 8. 一句话

> 判定、证据、地址治理这三样是**超配**的；执行面是**欠配**的，且欠的那一项恰好需要一台有 docker 的机器。
> 方案能推进到这里，剩下的不是设计问题，是环境与决策。
