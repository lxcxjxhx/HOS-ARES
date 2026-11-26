# HOS-ARES

<p>
  <a href="https://github.com/lxcxjxhx/HOS-ARES/actions/workflows/verify.yml"><img src="https://github.com/lxcxjxhx/HOS-ARES/actions/workflows/verify.yml/badge.svg" alt="verify" /></a>
  <a href="https://github.com/lxcxjxhx/HOS-ARES/actions/workflows/build-exe.yml"><img src="https://github.com/lxcxjxhx/HOS-ARES/actions/workflows/build-exe.yml/badge.svg" alt="build-exe" /></a>
</p>

可验证渗透验证内核。判定是纯函数、结论带哈希链证据、地址不进配置。

说人话：**它不会替你"感觉"有没有漏洞，它给你一条能复查的证据链**。

---

## 3 分钟跑起来

```bash
# 1. 装依赖（Python 3.11+）
pip install -e ".[dev]"

# 2. 环境自检（25 项，缺条件的记 skip 不记 fail）
hos-ares doctor

# 3. 坐下干活
hos-ares chat
```

未安装时把 `hos-ares` 换成 `python -m hos_ares_cli`，效果相同。

`hos-ares chat` 里可以直接说：

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
| **自动化渗透** | `hos-ares run --target <url> --openapi <spec>` | `report.md` / `findings.json` / `report.sarif` / `report.manifest.json` / `capsules/` |
| **智能体渗透** | `hos-ares hunt --target <url> --openapi <spec>` | `hunt.md` / `hunt.json` / `hunt.manifest.json` / `coverage.json` / `capsules/` |
| **企业编排** | `hos-ares platform --profile profiles/<档名>/profile.yaml --openapi <spec>` | `platform-report.md` / `platform-report.manifest.json`（逐资产 + 审计五表） |
| **对话界面** | `hos-ares chat` | 会话事件（`task_events`），可 `--resume` |

四份清单同形（`sections` + `files` + `manifest_sha256`），一条命令就能核：

    hos-ares replay --manifest <产物目录>

它逐文件核 sha256、并先核清单自哈希；输出会写清"挡得住手误，挡不住有意改"。

### 退出码（写脚本 / 接 CI 用）

三条主命令的退出码是**分开的语义**，别把 1 和 2 都当"失败"：

| 命令 | 0 | 1 | 2 | 3 |
|---|---|---|---|---|
| `hos-ares run` | 跑完并产出交付物 | 一项都没跑成；或**0 项验证成功且有执行失败**（目标不可达等）；或证据自校验失败 | 输入有问题（契约/目标/档） | — |
| `hos-ares hunt` | 本轮结束（含模型降级） | 契约里没有带语义标签的 operation | 输入有问题 | — |
| `hos-ares platform` | 全部资产跑完 | 有资产退出码非 0（报告里标 —） | 输入有问题 | **审批 PENDING，未执行** |

`3` 单独留出来，是为了让 CI 能区分"等人签字"与"跑挂了"——前者不该报警。

长驻命令（`chat` / `agent` / `console` / `tui`）只有两档：`0` = 正常结束或 Ctrl+C 中断；
`2` = 起不来（参数不对 / 端口被占 / 工作根不存在）。

这张表有对应的可执行契约：`tests/integration/test_exit_codes_contract.py`（12 格逐格真跑，改坏了会红）。


配合：

```bash
hos-ares console             # 只读控制台（11 个只读操作）
hos-ares tui                 # 终端三面板
hos-ares hunt --target …     # 边跑边打印
hos-ares parity              # 三对标总验收（5 面 × 22 项）
hos-ares replay --capsule …  # 证据重放
```

一条命令出演示成片（需要 ffmpeg + playwright）：

```bash
make demo-record            # → out/demo/hos-ares-tour.mp4
```

`make demo` 是**端到端演示**（发现 → 判定 → 证据 → 报告，见 `docs/delivery/quickref.md`）；
成片与演示是两件事，各有各的目标（B239）。

---

## 现在能做什么

| 能力 | 状态 | 证据 |
|---|---|---|
| 纯函数判定 + 三条件 + control | ✅ | `hos-ares parity` 的 V1 |
| 结论哈希链 + 六态 replay | ✅ | `hos-ares replay` |
| 地址零常量（`RouteRef` 无 url/host 字段） | ✅ | `hos-ares parity` 的 V3 |
| 浏览器渲染执行体（真 Chromium） | ✅ | `tests/integration/test_browser_render.py` |
| 容器真隔离（宿主不可见 / 只读挂载 / 容器内超时） | ✅ | `tests/integration/test_sandbox_isolation.py` + `hos-ares parity` 的 E1 |
| 容器级并行（每任务一容器，与串行逐条相同） | ✅ | `tests/integration/test_parallel_containers.py` + `hos-ares parity` 的 E5 |
| 工具面统一（agent 与工作台同一套） | ✅ | `hos-ares doctor` 的 tool-surface |
| 上下文压缩（降幅 ≥30%） | ✅ | `tests/unit/test_l1a_injection.py` |
| Playbook 资产 + 成功率实测 | ✅ | `playbooks/` |
| 插件装→用→升级回滚→卸 | ✅ | `tests/unit/test_plugin_lifecycle.py` |
| 审批落库 + 事件留痕 | ✅ | `tests/unit/test_approval_persistence.py` |
| 交互式会话（说人话 / 计划 / diff / 多步） | ✅ | `tests/integration/test_chat_*.py` |
| 统一 CLI（13 个子命令） | ✅ | `hos-ares --help` |

## 现在**不**能做什么

写清卡点，不写含糊的"待定"：

| 项 | 卡在哪 | 翻绿条件 |
|---|---|---|
| 真模型服从性 | 链路已验（本地假服务），**模型会不会守窄 schema 未知** | 配 `HOS_ARES_MODEL_BASE_URL` + `HOS_ARES_MODEL` 后跑一次 |
| 真实目标召回 0.33 | 结构推断已接线（靶场实测 1→3 个可跑 oracle），**外部指标未复测** | 能访问外部目标的环境 |
| 容器运行时依赖 docker/WSL | 宿主 docker 优先；没有就得有 WSL 发行版 + podman | 两者都没有时退回 Job Object（资源隔离，非容器），`isolation` 如实降级 |
| 容器池复用 | 容器级并行每任务起一个容器（约 3 秒/任务，镜像已缓存） | 任务数远大于 3 时才值得做 conmon 生命周期复用 |
| 资产发现/扫描 | **策略性不做**（`D-H-03`） | 不做 |
| IDE 插件 | **不做**（离线包要零依赖） | 不做 |

> 容器真隔离**已不在这一列**（B536）：宿主有 docker 就先用它；没有就在 Windows 上
> 找 WSL 发行版里的 podman（`wsl -d <发行版> --exec podman`）并真起容器——
> 容器内写 `/tmp` 宿主不可见、只读挂载写不动、超时在容器内触发。
> `isolation` 如实标 `container`；Job Object 档仍标 `job-object`，两者不混。
> 装法：`wsl -d <发行版> -- sudo apt install podman uidmap slirp4netns fuse-overlayfs`。

---

## 接模型（可选，B226）

```bash
export HOS_ARES_MODEL_BASE_URL=http://127.0.0.1:4000/v1   # OpenAI 兼容端点
export HOS_ARES_MODEL=deepseek-chat
export HOS_ARES_MODEL_API_KEY=sk-***                     # 需要鉴权时才设
hos-ares chat          # 启动行会显示 model: <名字>；不设则显示 model: rules
```

三条纪律：**没配就不联网**（`from_env()` 返回 None，自动降级到规则层）；**一步一次调用**
（`tool_calls_per_step=1`，不许一个 step 串多个工具）；**只认白名单工具名**（`SCHEMAS ∪ surface_names()`）。
`hos-ares agent` 是指令模式，不调模型。

---

## 接 MCP（可选，B522）

外部工具走 MCP（JSON-RPC over stdio）。一行配置就能接上：

```json
{"servers": {"codegraph": {"command": ["codegraph", "serve", "--mcp"]}}}
```

```bash
export HOS_ARES_MCP_CONFIG=$HOME/.hos-ares/mcp.json   # 也可以 --mcp-config <文件>
hos-ares chat
```

启动时会打印每个 server 接了几个工具；工具名形如 `mcp__codegraph__explore`，
和内置工具（`read`/`write`/`edit`/`patch`/`sh`）一样进白名单。

三条纪律：**起不来的 server 只警告不中断**（外部能力缺席不该让会话开不了）；
**调用有超时**（默认 20 秒，server 不回话就报错，不会一直等）；
**返回是数据不是指令**——server 说的话和网页内容同级。

配了却连不上，`hos-ares doctor` 的「模型后端」项当场报 FAIL 并给出可粘的 `curl` 探测命令——不用等到第一次
`hos-ares chat` 才发现；没配记 skip（规则层可用，不算缺陷）。报错按状态码指到具体那一项：401/403 是
`HOS_ARES_MODEL_API_KEY`、404 是 `base_url` 少了 `/v1`；配了 key 会带上它去探（不然 401 会冤枉成"检查 key"），
端点没有 `/models` 只记 skip（能连是事实，核对不到模型名也是事实）。`hos-ares chat` 里模型层失败会原样说出原因，再降级 rules。

---

## 文档地图

| 想知道 | 看 |
|---|---|
| 判定内核的四条不变量 | `plan-hos-ares/`（冻结只读的规格包） |
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
hos-ares doctor                           # 环境自检
hos-ares parity                           # 5 面 × 22 项
python scripts/lint/ledger_check.py      # 批次账本诚实性
node plan-hos-ares/check-plan.cjs        # 规划包结构
```

批次账本在 `.hos-ares/batches.jsonl`，只增不改。

**当前状态总账**：[`docs/delivery/STATUS.md`](docs/delivery/STATUS.md) ——
能做什么、验过什么、还有什么没验、差谁给什么，每条数字都带复核命令。
要拍板的口径全在 [`plan-hos-ares/H33-待裁规格事项.md`](plan-hos-ares/H33-待裁规格事项.md)。
