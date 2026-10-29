# NEXT · 续接指针

> 权威层：**续接指针**（只写"现在从哪继续、最近证据、下一道门"）。
> 每批退出时更新本文件。上游：`总设计案.md` + `蓝图集/BLUEPRINT_INDEX.md`。

## 当前状态

**批次**：`B5 · TierA 前 4 类 oracle`
**波次**：W1 内核骨架 **已完成** → 进入 W2 能力扩容
**完成度**：4/17 批有 DoD 证据

```
W1 ▣B1 ▣B2 ▣B3 ▣B4   ← 冷启动门已过
W2 ▢B5 ▢B6 ▢B7 ▢B8 ▢B9
W3 ▢B10 ▢B11 ▢B12 ▢B13
W4 ▢B14 ▢B15 ▢B16 ▢B17
```

## W1 冷启动门：四条证据全部实测

| 门 | 要求 | 实测结果 |
|---|---|---|
| 1 | 至少 1 条 `VERIFIED` 发现 | ✅ 3 条 capsule（F001/F002/F003）均 `VERIFIED` |
| 2 | 该发现带 capsule | ✅ `self_check()["verified_without_capsule"] == []` |
| 3 | `make replay` 四态之一 | ✅ `REPLAYED_OK`（dry-run 与 offline-fixture 均过） |
| 4 | 缩 scope 后拒绝且**零请求** | ✅ guard 抛出后发送函数 0 次调用；replay 出 `REPLAY_BLOCKED` |

## 各批 DoD 证据（全部实测）

### B1 · 骨架与契约
| DoD | 命令 | 结果 |
|---|---|---|
| 契约测试 | `pytest tests/unit/test_contracts.py` | **4 passed** |
| `POST /tasks` 返回 id | 真实 HTTP → `127.0.0.1:8099` | `task_id=01M63BDM4D8A4TP9EN9Z7NV8F9`（26 位 ULID） |
| 两次同 idem_key 同 id | 同上 | `同 id: True`、`reused: True`、库内仅 1 行 |
| `lint-nodata-stub` | `python scripts/lint_nodata_stub.py` | `0 violations` |

### B2 · 调度
| DoD | 结果 |
|---|---|
| `kill -9` worker 后 35s 内任务被接管 | **32.10s**（真起子进程再真杀；先断言"未过期时抢不到"，再断言"过期后接得到"） |
| `transition` 并发 2 线程只 1 成功 | **恰好 1 True / 1 False** |
| `pytest tests/unit/test_scheduler.py` | **18 passed** |

### B3 · guard
| DoD | 结果 |
|---|---|
| 缩 scope 后拒绝且零请求 | **发送函数 0 次调用**（计数器证明） |
| 令牌桶 100 并发 ≈ 20/s | **实测 3.00s**（理论 3.0s：40 burst 立即 + 60÷20） |
| 越界记 `scope ok=0` | 落库验证 + `scope_violations == 1` |
| `pytest tests/unit/test_guard.py` | **29 passed** |

### B4 · 证据
| DoD | 结果 |
|---|---|
| 同输入两次 seal 同哈希 | ✅ 字节完全一致 |
| 改一字节报 TAMPERED | ✅ `exit=2`、`VP-5001` |
| `--offline-fixture` 断网可跑 | ✅ `REPLAYED_OK` + `VERIFIED` |
| `--dry-run` 只校验不发包 | ✅ `steps_resent=0` |
| scope 改小 → `REPLAY_BLOCKED` | ✅ 安全行为，非失败 |
| 单测 + CLI e2e | **18 + 7 passed** |

## 本阶段修掉的三个真缺陷（不是测试写错）

| # | 缺陷 | 为什么危险 | 修法 |
|---|---|---|---|
| 1 | `verify_integrity()` 拿"现算值"和"现算值"比 | **防篡改形同虚设**——任何篡改都通不过检测才怪 | 保留文件自称的 `chain.self_sha256`，与现算值比对 |
| 2 | `capsule_from_dict` 丢弃 `created_at` | 合法 capsule round-trip 后自我报篡改（序列化把它写成"现在"） | `_parse_rfc3339()` 还原 |
| 3 | `to_payload()` 直接返回 `assertion` 引用 | 调用方能透过返回值改 capsule 内部状态，不可变性被穿透 | `copy.deepcopy()` |

另修两处工程问题：`store.transition/lease_next` 并发缺原子锁（全量跑偶发双赢）；`lint_noleak` 把 `05 §1.1` 要求的私网 deny CIDR 误判为泄漏。

## 下一道门：W2 A6 覆盖（B9 结束时）

14 类里 11 类可跑；40 op → 200 用例；覆盖矩阵无空格（有空格必须写明原因）。

## B5 开工要点

- 只读：`18 §1`、`07 §0`、`25 §3.3`
- 允许改：`veripen_oracles/{sqli_bool_diff,idor_cross_account,ssrf_oob,jwt_replay}/`、`oob/`
- DoD：靶场 4 类各 ≥1 VERIFIED；负样本全 REFUTED；单测 ≥4 正 + 2 负/类（夹具来自 `make record`，**不许手写**）
- 硬约束：`git diff --stat veripen_core/` 必须为空
- **环境阻塞**：Docker 未装 → 靶场（DVWA/juice-shop/crAPI/vAmPI/WebGoat）起不来。B5 的策略：先做 oracle 的纯函数 judge + 确定性夹具（来自真实录制的 JSON），靶场联调留到 Docker 就位；夹具必须带 `provenance` 块（`26 §2`）。

## 遗留

| # | 项 | 处理 |
|---|---|---|
| 1 | Docker 未安装 | B13 离线包前必须解决；B5 用真实录制夹具替代现场靶场 |
| 2 | Redis 未安装 | 当前用 SQLite 原子租约 + LocalTokenBucket（已标注仅单 worker）；交付档切 Redis |
| 3 | JADX 未配 | HOS-ARES 不打 APK，暂不装 |
| 4 | `profile_loader` 未接 | B1 用 `None` 走"不校验 scope"路径；待 B5 或 B13 接入 |
