# HOS-ARES · VeriPen

> **可审计、可复现、可离线部署的渗透验证内核** —— 不是扫描器，是验证器。

[![Python](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-Apache--2.0-green)](LICENSE)
[![Batches](https://img.shields.io/badge/batches-1%2F17-orange)](蓝图集/BLUEPRINT_INDEX.md)

## 一句话

> 一份 `scope.yaml` + 一份客户档进来说明打谁、打到什么程度；
> 确定性 oracle 负责验证、LLM 只负责找路；
> 每条发现产出可 `replay` 的 proof capsule；
> 换客户只换 YAML，内核不动。

## 三条不许动的决定

| # | 决定 | 为什么 |
|---|---|---|
| **S1** | 判定是纯函数（`oracle.judge` 无 IO/时钟/随机） | 可信度、可测性、离线演示全挂在它上面 |
| **S2** | 三条件合取才出 VERIFIED（probe ∧ baseline ∧ control） | FP 门槛 0 的唯一实现方式 |
| **S3** | 客户差异只进 profile（`core diff = 0`） | 这是"能卖第二家"的机械判据 |

## 八条可演示断言

| # | 断言 | 命令 |
|---|---|---|
| A1 | 能卖第二家 | `make demo` / `make demo-alt` |
| A2 | 差异没漏进代码 | `make lint-noleak` |
| A3 | 内网能装 | `make package-offline && make doctor` |
| A4 | 不是"我说有洞" | `make replay ID=n` |
| A5 | 有数 | `make bench` |
| A6 | 覆盖够宽 | `make coverage-matrix` |
| A7 | 接口能自动覆盖 | `make cases CASES=openapi.yaml` |
| A8 | 模型不是瓶颈 | `make bench PLANNER=scripted` |

## 快速开始

```bash
# 安装
pip install -e ".[dev]"

# 跑契约测试（架构没走样）
pytest tests/unit/test_contracts.py

# 全量单测 + 两道 lint
make test

# 看 17 批进度
make batch-status
```

起 API（本地）：

```bash
uvicorn "api.app:create_app" --factory --port 8000
curl -X POST http://127.0.0.1:8000/v1/tasks \
  -H 'Content-Type: application/json' \
  -d '{"engagement_id":"ENG-2026-001","profile":"demo-a",
       "asset_ids":["web-01"],"oracle_ids":["sqli_bool_diff"]}'
```

## 架构

```
veripen_cli        run / replay / bench / doctor / report / cases / stats / lint-noleak
      │
      ├── veripen_oracles    14 类确定性验证器（A 6 / B 5 / C 3）
      ├── veripen_contract   OpenAPI/GraphQL → 用例生成
      │
      └── veripen_core       调度 · agent 循环 · guard · 证据 · Oracle 协议   ← 零客户串
                 │
                 └── profiles/<name>/*.yaml   纯数据，唯一允许出现客户差异的地方
```

**依赖铁律**：`core` 不 import `oracles`；`core` 与 `oracles` 都不得出现客户标识；profiles 是纯数据；唯一出口是 `cli`。

## 权威链

| 层 | 文件 | 回答什么 |
|---|---|---|
| 总设计案 | [`总设计案.md`](总设计案.md) | 做什么、为什么、不做什么 |
| 蓝图集 | [`蓝图集/BLUEPRINT_INDEX.md`](蓝图集/BLUEPRINT_INDEX.md) | 按什么边界做到什么证据（17 批） |
| 续接指针 | [`NEXT.md`](NEXT.md) | 从哪继续、最近证据、下一道门 |
| 规格库 | [`docs/spec/`](docs/spec/) | 27 篇细则，被引用而非当权威 |

**进度事实源**：`.veripen/batches.jsonl`（唯一）。看板 `make batch-status` 只是它的投影。

## 边界（冻结）

```
靶场   DVWA / OWASP juice-shop / crAPI / vAmPI / WebGoat + 适配层；不打外网
漏洞   14 类；默认启用 9 类，4 类需授权
客户档 demo-a（企业 SaaS）· demo-b（车云 API）
不做   内网 AD / C2 / 车机多模态 / 白盒全量 / 模型微调 / 交互式工具 /
       多租户 / K8s / SAML 全自动化
```

## 与其他渗透 agent 的差异

开源生态里最接近的对标物是 `usestrix/strix`（LLM 驱动、报告叙述判定）。
本项目的判定链是**确定性 oracle + 三条件合取 + 可 replay 的 capsule**——
这条链在开源生态里没有先例（最接近的只有 vcrpy cassette 与 SARIF codeFlows，且都止步于"记录"）。

详见 [`docs/research/01-open-source-pentest-agent-survey.md`](docs/research/01-open-source-pentest-agent-survey.md)。

## 许可证

Apache-2.0
