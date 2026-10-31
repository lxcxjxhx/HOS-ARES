# docs/research/re · 逆向/移植证据根

> 本目录是 `plan-veripen/28-逆向对标与借鉴规格.md` 的**唯一整目录豁免项**（`check-noroute` 的 allowlist）。
> 只在 `evidence/` 与 `notes.md` 的引用块里出现 URL/路径常量；`extracts/` 不豁免。

## 目标台账

| slug | 仓库 | commit | 许可 | 面 | 状态 |
|---|---|---|---|---|---|
| rea | morluto/rea | `250dc81c` | MIT | F1 F6 | 源码已 pin |
| strix | usestrix/strix | `6b7ae2eb` | Apache-2.0 | F1 F3 F6 | 源码已 pin |
| promptfoo | promptfoo/promptfoo | `70296a68` | MIT | F5 | 源码已 pin |
| garak | NVIDIA/garak | `4b7761c2` | Apache-2.0 | F5 | 源码已 pin |
| ai-infra-guard | Tencent/AI-Infra-Guard | `851e3f29` | Apache-2.0 | F1 F2 | 源码已 pin |
| pyrit | microsoft/PyRIT | `6d225660` | MIT | F3 | 源码已 pin |
| deepteam | confident-ai/deepteam | `6306f0ff` | Apache-2.0 | F5 | 源码已 pin |
| cyber-harness | chainreactors/cyber-harness | `89f8af5e` | AGPL-3.0（只读结构） | F1 F3 | 源码已 pin |

每个子目录含：`manifest.json`（身份/哈希/面）、`notes.md`（过程）。
分析产出按 `28 §5` 七段式写入 `extracts/`（暂无，按批次补）。

## 复现

```bash
bash scripts/vendor-fetch.sh          # 按 vendor.lock.json 拉全部源码到 vendor/<slug>/
```

集成映射见 `docs/research/02-ecosystem-port-integration.md`。
