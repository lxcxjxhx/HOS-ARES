# NEXT · 续接指针

> 权威层：**续接指针**（只写"现在从哪继续、最近证据、下一道门"）。
> 每批退出时更新本文件。上游：`总设计案.md` + `蓝图集/BLUEPRINT_INDEX.md`。

## 当前状态

**批次**：`B3 · guard`
**波次**：W1 内核骨架（B1–B4）
**完成度**：2/17 批有 DoD 证据

## 最近证据（B1）

| DoD 项 | 命令 | 结果 |
|---|---|---|
| 契约测试 3 绿 | `pytest tests/unit/test_contracts.py` | **4 passed**（含 3 条契约 + 1 条不可变性） |
| `POST /tasks` 返回 id | 真实 HTTP 到 `127.0.0.1:8099` | `task_id=01M63BDM4D8A4TP9EN9Z7NV8F9`（26 位 ULID） |
| 两次同 idem_key 同 id | 同上 | `同 id: True`、`reused: True`、库里只有 1 行 |
| `make lint-nodata-stub` 0 命中 | `python scripts/lint_nodata_stub.py` | `0 violations` |
| 附带：`lint-noleak` | `python scripts/lint_noleak.py` | `0 violations`（扫到 token `DEMO-A-001` / `demo-a` 均未泄漏） |
| 附带：全量单测 | `pytest tests/unit` | **19 passed** |

证据 JSON：真实 HTTP 往返响应已在 B1 批次记录中。

## 下一道门：W1 冷启动门（B4 结束时）

必须同时在一次运行中看到：
1. 至少 1 条 `VERIFIED` 发现
2. 该发现带 capsule（`state=VERIFIED AND capsule_id IS NULL` 应查为 0）
3. `make replay ID=<n>` 返回 `OK`（四态之一）
4. 缩小 scope 后 agent **拒绝退出**并返回 `BLOCKED`（不是"尽力而为"）

## B2 开工要点

- 只读：`04 §1–§4`、`03 §2`
- 允许改：`veripen_core/{lease,scheduler}.py`、`worker/`
- 常量已定：心跳 5s / TTL 30s / 扫描 5s / max_attempts 3 / 退避 1s,4s,16s(±20%)
- DoD：`kill -9` worker 后 35s 内任务被接管；`transition` 并发 2 线程只 1 成功
- **环境阻塞**：Redis 未装、Docker 未装。租约需 Redis 原子操作 → 先做本地 SQLite 租约实现并注明"交付档切 Redis"，或先装 Redis。

## 遗留

| # | 项 | 处理 |
|---|---|---|
| 1 | Docker 未安装 | B13 离线包前必须解决；B1–B12 可仅用进程内实现 |
| 2 | Redis 未安装 | B2 决策：SQLite 原子租约先行，Redis 作为交付档 |
| 3 | JADX 未配 | APK 分析才需要；HOS-ARES 不打 APK，暂不装 |
| 4 | `profile_loader` 未接 | B1 用 `None` 走"不校验 scope"路径；B3 guard 接入 |
