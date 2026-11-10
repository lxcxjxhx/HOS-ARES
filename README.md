# HOS-ARES

可验证渗透验证内核。判定是纯函数、结论带哈希链证据、地址不进配置。

说人话：**它不会替你"感觉"有没有漏洞，它给你一条能复查的证据链**。

---

## 3 分钟跑起来

```bash
# 1. 装依赖（Python 3.11+）
pip install -e ".[dev]"

# 2. 环境自检（23 项，缺条件的记 skip 不记 fail）
veripen doctor

# 3. 坐下干活
veripen chat
```

未安装时把 `veripen` 换成 `python -m veripen_cli`，效果相同。

`veripen chat` 里可以直接说：

```
> 看下 out/x.txt
> 往 out/y.txt 写 hello
> 把 out/y.txt 里的 hello 改成 bye 然后读一下
> 执行 python -V
```

---

## 双击就能看（安装包 / 免安装 exe）

不想装 Python 的人走这条路：从 [Releases](../../releases) 下载 **`HOS-ARES-Setup-<版本>.exe`**，
装完开始菜单与桌面就有快捷方式，双击直接是控制台界面。

```
安装包装完后：开始菜单 / 桌面 "HOS-ARES" → 起只读控制台 → 自动开浏览器
免安装单文件：HOS-ARES.exe            → 同上，不需要安装
```

界面就是三支柱的出口（覆盖 / 发现 / 未验证 / 路由 / 审计 / 审批 / 边界）：
它**只读**，唯一的写入是审批决定（有 `--db` 时落进 `task_events`）。

```text
HOS-ARES.exe                        # 默认向系统要空端口，自动开浏览器
HOS-ARES.exe --record out/hunt.json # 指定运行产物（hunt.json 或 findings.json）
HOS-ARES.exe --port 18080           # 固定端口
HOS-ARES.exe --no-browser           # 不自动开浏览器（无头/调试）
```

安装包里还带一份 `快速开始.txt`（装到安装目录与开始菜单）。

产物由 [`.github/workflows/build-exe.yml`](.github/workflows/build-exe.yml) 自动构建，
**构建即自检**：

```text
1) PyInstaller 打单文件
2) 起服务 → GET /ui/app → 断言 200 + <title> + id="nav"
3) Inno Setup 打安装包
4) 静默装一遍 → 跑装出来的 exe → 断言页面 → 静默卸载 → 断言无残留
5) 上传 artifact；推 tag 时两个产物都附到 Release
```

本地复现：

```bash
pip install pyinstaller
python -m PyInstaller packaging/HOS-ARES.spec --noconfirm      # dist/HOS-ARES.exe
ISCC.exe /DMyAppVersion=0.1.0 packaging/HOS-ARES.iss           # packaging/out/HOS-ARES-Setup-*.exe
```

---

## 一条命令验完整仓

```bash
python scripts/verify.py              # 快闸门（约 1 分钟）：mypy + 7 道 lint + 账本 + 计划结构 + bench 冒烟
python scripts/verify.py --with-tests # 追加分段 pytest（本机全量约 30 分钟）
python scripts/verify.py --list       # 看清单
```

退出码三档，**跳过不算通过**：

```
0  全绿
1  有失败（会点名是哪道）
2  没有失败，但有跳过（缺条件的环境不算通过）
```

CI（[`.github/workflows/verify.yml`](.github/workflows/verify.yml)）调的是**同一份清单**：
ubuntu 上跑快闸门，windows 上把测试分三段跑——避免"CI 跑的"和"本地跑的"不是同一串命令。

---

## 三条主线

| 面 | 命令 | 产物 |
|---|---|---|
| **自动化渗透** | `veripen run --target <url> --openapi <spec>` | `report.md` / `findings.json` / `capsules/` |
| **企业编排** | `veripen platform --profile profiles/<档名>/profile.yaml --openapi <spec>` | `platform-report.md`（逐资产 + 审计五表） |
| **对话界面** | `veripen chat` | 会话事件（`task_events`），可 `--resume` |

配合：

```bash
veripen console             # 只读控制台（10 个只读操作）
veripen tui                 # 终端三面板
veripen hunt --target …     # 边跑边打印
veripen parity              # 三对标总验收（5 面 × 22 项）
veripen replay --capsule …  # 证据重放
```

一条命令出演示成片（需要 ffmpeg + playwright）：

```bash
make demo                   # → out/demo/hos-ares-tour.mp4
```

---

## 现在能做什么

| 能力 | 状态 | 证据 |
|---|---|---|
| 纯函数判定 + 三条件 + control | ✅ | `veripen parity` 的 V1 |
| 结论哈希链 + 六态 replay | ✅ | `veripen replay` |
| 地址零常量（`RouteRef` 无 url/host 字段） | ✅ | `veripen parity` 的 V3 |
| 浏览器渲染执行体（真 Chromium） | ✅ | `tests/integration/test_browser_render.py` |
| 工具面统一（agent 与工作台同一套） | ✅ | `veripen doctor` 的 tool-surface |
| 上下文压缩（降幅 ≥30%） | ✅ | `tests/unit/test_l1a_injection.py` |
| Playbook 资产 + 成功率实测 | ✅ | `playbooks/` |
| 插件装→用→升级回滚→卸 | ✅ | `tests/unit/test_plugin_lifecycle.py` |
| 审批落库 + 事件留痕 | ✅ | `tests/unit/test_approval_persistence.py` |
| 交互式会话（说人话 / 计划 / diff / 多步） | ✅ | `tests/integration/test_chat_*.py` |
| 统一 CLI（12 个子命令） | ✅ | `veripen --help` |

## 现在**不**能做什么

写清卡点，不写含糊的"待定"：

| 项 | 卡在哪 | 翻绿条件 |
|---|---|---|
| 容器真隔离 | 接口/策略/BLOCKED 已验，**真隔离未验** | 一台装了 docker 或 podman 的机器，跑 `make exec-isolation` |
| 容器级并行 | 进程内已验（三任务指纹与串行一致），**容器级未验** | 同上 |
| 真模型服从性 | 链路已验（本地假服务），**模型会不会守窄 schema 未知** | 配 `VERIPEN_MODEL_BASE_URL` + `VERIPEN_MODEL` 后跑一次 |
| 真实目标召回 0.33 | 结构推断已接线（靶场实测 1→3 个可跑 oracle），**外部指标未复测** | 能访问外部目标的环境 |
| 资产发现/扫描 | **策略性不做**（`D-H-03`） | 不做 |
| IDE 插件 | **不做**（离线包要零依赖） | 不做 |

---

## 文档地图

| 想知道 | 看 |
|---|---|
| 判定内核的四条不变量 | `plan-veripen/`（冻结只读的规格包） |
| 22 项能力矩阵与对标口径 | `plan-hos-ares/H01-能力矩阵与对标口径.md` |
| 25 批排期与判据 | `plan-hos-ares/H07-批次总表与排期.md` |
| 二期接线（B60–B83） | `plan-hos-ares/H11`–`H25` |
| 界面/成片说明 | `docs/delivery/console-tour.md`、`tour.md`、`terminal-tour.md` |
| 包边界与 PR 流程 | `plan-hos-ares/H10-PR与文件管理.md` |

---

## 验收

```bash
pytest tests/unit -q                     # 单测
pytest tests/integration -q              # 集成（含靶场）
veripen doctor                           # 环境自检
veripen parity                           # 5 面 × 22 项
python scripts/lint/ledger_check.py      # 批次账本诚实性
node plan-hos-ares/check-plan.cjs        # 规划包结构
```

批次账本在 `.veripen/batches.jsonl`，只增不改。
