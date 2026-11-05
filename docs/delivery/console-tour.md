# 控制台巡检录屏（B65）

一条命令产出**能看的界面**：10 个只读页、真数据、真 Chromium 渲染。

```bash
python scripts/demo/record_console.py
```

产物在 `out/demo/`：

| 文件 | 用途 |
|---|---|
| `console-tour.mp4` | 1280×720 / 27.4s / H.264，每页停留 2.5s |
| `pages/NN-<op>.png` | 10 张逐页截图，可直接贴进报告 |
| `manifest.json` | 页序列、关键数值、视频规格 —— **机器可判的那份** |

## 为什么是三件套

一段 mp4 只能证明"我看过"，不能证明"数据是真的、页面顺序对、下次还会一样"。
所以清单才是主产物：

- **视频**给感受；
- **PNG** 给引用（报告、评审、issue）；
- **manifest** 给判据（`tests/unit/test_demo_manifest.py` 直接读它做断言）。

## 演示数据怎么来的

脚本自建 `out/demo/tour.db` 并喂给控制台：

- 4 条 oracle 事件（2 通过 / 2 失败，带 evidence 与 verdict）
- 审批四态各一行：`approved` / `rejected`（带理由）/ `pending`（带操作框）/ `expired`
- 10 条 `task_events`——所以 audit 页有内容，不是 `unavailable`

**用真库不用内存态**：audit 页读的是 `task_events` 表，内存态的
`ConsoleState` 只能骗页面，骗不了判据 C。

## 看什么

| 页 | 该看到什么 |
|---|---|
| overview | 三卡片：资产与范围 / 结论计数 / 覆盖缺口；顶部"覆盖率 1/3"横幅 |
| findings | 三条结论，四态徽标（已确认 / 未确认 / 阻塞 / 未执行） |
| finding | 详情五段 + 证据链 |
| unverified | "未验证"独立成页，**不冒充高危** |
| coverage | 覆盖矩阵 |
| routes | 绑定表（ref 前 24 位 / 标签 / 来源 / 置信档 / TTL） |
| **audit** | 事件表：时间 / 谁 / 做了什么 / 对象 / 结果（B64 刚接上数据源） |
| bounds | 越界事件（正常为 0，红字是事故不是统计） |
| **approvals** | 四态卡片；pending 那条带"必填理由 + 批准/拒绝" |

![概览页](../out/demo/pages/01-overview.png)
![审计页](../out/demo/pages/08-audit.png)
![审批页](../out/demo/pages/10-approvals.png)

> 图片路径相对仓库根；若渲染器不解析，直接打开 `out/demo/pages/`。

## 判据（H12 B65）

| # | 判据 | 怎么验 |
|---|---|---|
| A | 视频 ≥25s、1280×720、H.264 | `validate_manifest` + `ffprobe` |
| B | 10 页按 `OPS` 顺序各出现一次 | manifest 的 `page_order` |
| C | findings / events / approvals 三项都 > 0 | manifest 的 `counts` |
| D | 两次运行页序列与 counts 逐字相同 | 手动跑两次比对（**不要求视频字节相同**，编码器非确定） |
| E | 全程只连 `127.0.0.1` | 端口从 18080 顺序取，不解析外部域名 |

实测（2026-11-05）：

```
page_order 一致: True
counts 一致: {'findings': 3, 'events': 11, 'approvals': 4}
视频时长: 27.43 -> 27.43
```

## 期间修掉的三处真缺陷

录屏的价值就在这里——**画面一出来，问题全露**：

1. **audit 页是裸 JSON**。10 页里只有它落到 `renderGeneric` 兜底，
   因为 `renderAudit` 从来没写过。已补表格（时间/谁/做了什么/对象/结果）。
2. **端口随机绑定撞上 Chromium 黑名单**。`bind(0)` 分到 6667 → `ERR_UNSAFE_PORT`，
   录制直接失败。改为从 18080 顺序探测。
3. **演示数据用 1970 秒造数**。页面按 TTL 判 pending 是否过期，
   `requested_at=1002` 让"待批"当场显示成"已过期"。改用真实 `now`。

## 取舍

- **不用 Playwright 的 `record_video`**：产出 webm、起停与页面稳定性耦合、容易录进白屏。
  逐页截图 + ffmpeg 拉时长，能保证每页至少一帧干净，且天然产出可引用的 PNG。
- **动画录不进去**：页面本来没有过渡，这个取舍没有代价。
- **不追求视频字节一致**：x264 编码非确定性，判据只咬页序列与关键数值。
