# H16 · 平台接 profile（B72）

> **问题**：`profiles/demo-a|demo-b` 两个客户档写好了 assets / scope / selectors /
> oracles / 审批策略，但 `cmd_platform` 至今靠 `--targets` 直给，
> **profile 只是躺在那里的 YAML**。
>
> 企业级的入口不是"能传一串地址"，是"按客户档跑"——档里写着允许打哪些面、
> 哪些动作要人批、engagement 叫什么。

## B72 · `platform --profile`〔P1，1 人天〕

| 项 | 内容 |
|---|---|
| 目标 | `veripen platform --profile profiles/demo-a/profile.yaml` 从档里读资产（符号→env）、oracle 白名单、审批策略、engagement 身份，跑完报告写明"按哪个档跑的" |
| 判据 | A：解析出资产列表与 oracle 白名单（报告可见）；B：`oracles.needs_approval` 非空时**自动要求审批**，不再依赖手工 `--require-approval`；C：报告头写 profile 名 / 版本 / engagement_id / profile_hash；D：资产符号未由环境注入时**退出 2 且不猜地址**（fail-closed）；E：`--profile` 与 `--targets` 同时给时明确报错，不静默取其一 |
| 产出 | `veripen_cli/cmd_platform.py`（`--profile` 分支）+ `tests/integration/test_platform_profile.py` |
| 依赖 | `veripen_core/profile.py`（`load_profile` / `assert_resolvable` / `allowed_oracles`） |
| PR | `b72/platform-profile` |
| 回滚 | 删掉 `--profile` 分支；`--targets` 路径不变 |

### 符号 → 地址的纪律

profile 里不写地址，只写符号（`ASSET_A1`）；真实值由部署期环境变量注入，
变量名由 `Profile.asset_env` 给出（`VP_ASSET_A1`）。

`assert_resolvable()` 是 fail-closed：缺一个就整批拒跑，**不允许"猜一个"**。
这条与 H2（零地址）一致——地址只能由运行时给出。

### oracle 白名单怎么落到执行

`cmd_run` 只有黑名单（`--exclude`），没有白名单。
本批不改 `cmd_run` 的接口，而是在 `cmd_platform` 里把白名单翻成黑名单：
`exclude = 全部 oracle - profile.enabled`。这样两个入口的语义仍一致，
且 `cmd_run` 的契约不变。

### 审批怎么从档里来

`oracles.needs_approval` 非空 ⇒ 本次运行必须有人批。
`--approve` 仍是"模拟 operator 签字"的唯一开关——档决定"要不要批"，
命令行决定"谁批了"。两者分工不能混。

## 与 B68 的关系

B68 建的是"多资产 + 审批 + 报告"的骨架，资产来自 `--targets`。
B72 把资产的来源换成客户档，其余（报告四节、审计五表、证据目录）完全复用。

## 验收命令

```bash
VP_ASSET_WEB_01=http://127.0.0.1:PORT python -m veripen_cli.cmd_platform \
  --profile profiles/demo-a/profile.yaml --openapi out/demo/spec.json
python -m pytest tests/integration/test_platform_profile.py -q
```

## 修订记录

| 日期 | 版本 | 变更 | PR |
|---|---|---|---|
| 2026-11-05 | `plan-hos-v1.0`（附录册） | 立册：B72 平台接 profile | 待提 |
