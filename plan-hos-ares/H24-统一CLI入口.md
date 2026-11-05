# H24 · 统一 CLI 入口（B82）

> 现在每个能力都要 `python -m veripen_cli.cmd_chat` / `cmd_run` / `cmd_platform`。
> Codex 是 `codex`，Claude Code 是 `claude`——**一个词进产品**。
>
> 这不是包装，是可用性：用户记不住九个模块名。

## B82 · `veripen <子命令>`〔P1，0.5 人天〕

| 项 | 内容 |
|---|---|
| 目标 | `python -m veripen_cli <子命令>` 与安装后的 `veripen <子命令>` 都能用；子命令表覆盖全部既有能力；`veripen --help` 一屏看懂 |
| 判据 | A：`python -m veripen_cli chat` / `run` / `platform` 能调起对应命令（参数原样透传）；B：未知子命令退出码 2 并列出可用；C：`veripen --help` 列出全部子命令且每个有一句话说明；D：既有 `python -m veripen_cli.cmd_*` 直调**完全不变**（不破坏脚本）；E：`pyproject.toml` 声明 `veripen` 入口脚本 |
| 产出 | `veripen_cli/__main__.py` + `pyproject.toml`（`[project.scripts]`）+ `tests/unit/test_cli_entry.py` |
| 依赖 | 各 `cmd_*.py` 的 `main(argv)` 签名（已统一） |
| PR | `b82/cli-entry` |
| 回滚 | 删 `__main__.py` 与 scripts 声明；直调路径不变 |

### 子命令表

| 子命令 | 落到 | 一句话 |
|---|---|---|
| `chat` | `cmd_chat` | 交互式 agent 会话 |
| `run` | `cmd_run` | 端到端渗透（给交付物） |
| `platform` | `cmd_platform` | 企业编排（多资产 + 审批 + 报告） |
| `hunt` | `cmd_hunt` | 边跑边打印 |
| `console` | `cmd_console` | 只读控制台 |
| `agent` | `cmd_agent` | 指令模式会话 |
| `tui` | `tui` | 终端三面板 |
| `doctor` | `cmd_doctor` | 部署自检 |
| `parity` | `cmd_parity` | 三对标总验收 |
| `playbook` | `cmd_playbook` | Playbook 资产 |
| `replay` | `cmd_replay` | 证据重放 |

### 为什么不做成 `click` / `typer`

零依赖纪律（`H04 §3.1`、离线包 A3）：`argparse` 是 stdlib，够用。
引入 CLI 框架会把"内网能装"变成"要先 pip"。

### 透传规则

子命令拿到 `argv[1:]` 后**原样**交给对应 `main()`——
不在中间层解析、不改写、不补默认值。中间层一旦开始"帮你补参数"，
`--help` 与实际行为就会分叉。

## 执行记录（2026-11-05）

```
$ python -m veripen_cli --help
子命令：chat run platform hunt console agent tui doctor parity playbook replay

$ python -m veripen_cli nosuch      → exit 2 + 列出可用
$ python -m veripen_cli doctor --only python   → 透传成功
$ python -m veripen_cli --version   → 0.1.0（与 pyproject 一致）
```

判据 A–E 全绿：`test_cli_entry.py` 7 passed（含"直调路径不变"与
"版本号与 pyproject 一致"）。

一处细节：`--version` 的版本号原来 fallback 到 `0.0.0` ——
`veripen_cli/__init__.py` 没有 `__version__`。补上并让测试锁住它与 pyproject 一致：
两个地方各写一份版本号，迟早会分叉。

## 验收命令

```bash
python -m veripen_cli --help
python -m veripen_cli chat --help
python -m veripen_cli nosuch
python -m pytest tests/unit/test_cli_entry.py -q
```

## 修订记录

| 日期 | 版本 | 变更 | PR |
|---|---|---|---|
| 2026-11-05 | `plan-hos-v1.0`（附录册） | 立册：B82 统一 CLI 入口 | 待提 |
