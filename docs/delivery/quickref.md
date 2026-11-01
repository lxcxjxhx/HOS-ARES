# VeriPen 一页速查

## 8 条命令

```
$ make up            # 起环境（数据库 / worker / 靶场）
$ make demo          # 端到端演示：发现 → 判定 → 证据 → 报告 → replay → 缩 scope
$ make demo-alt      # 换客户档跑同一份镜像（A1）
$ make bench         # 44 例 × 3 轮，FP=0 / 越界=0 / routeset 三轮一致
$ make replay-offline# 断网只走判定纯函数
$ make doctor        # 16 项自检，失败项会指出该改哪里
$ make lint          # 零地址 / 禁数据桩 / 绑定覆盖 / 默认关闭
$ make verify-report # 报告三视图零地址 + manifest 未手改
```

## 4 种故障对应

| 现象 | 先跑 | 大概率是 |
|---|---|---|
| **装不上** | `make doctor` | `default-off` 或 `bindings` 不过 → 看 fix 那列 |
| **跑不动** | `make demo-offline` | 网络/模型不可用 → 离线链路只走纯函数 |
| **结果可疑** | `make bench` + `make verify-report` | FP≠0 → 看负样本；manifest 不过 → 报告被改过 |
| **要立刻停** | 缩 scope 再跑 | `REPLAY_BLOCKED` + 零发包 = 门在工作；仍发包 = P0 |

## 三条不能让步的

1. 判定是纯函数 —— 同一 capsule 三轮 verdict 必须一致；
2. 地址不可推断 —— 报告/绑定页/capsule 里搜不到地址；
3. 面没了 ≠ 修好了 —— `ROUTE_UNRESOLVED` 不许写成「已修复」。
