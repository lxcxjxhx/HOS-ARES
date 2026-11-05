# H20 · 交互式 Agent 界面（B77）

> **用户反复强调的那件事**：要像 Codex / Claude Code / Cursor 那样**能对话、能看它干活**，
> 而不是敲 `read x.txt` 这种命令名。
>
> 前面 B67 的 `agent` 是"指令模式"——白名单动词 + 参数，本质是个交互式 shell。
> 那不算 agent 界面。

## 0. 什么是"agent 界面"，什么不是

| 不是 | 是 |
|---|---|
| 敲 `read path` 执行读文件 | 说"看下 out/x.txt 里写了什么"，它自己决定用哪个工具 |
| 一次一条命令，无计划 | 先说计划（"我要先读文件、再改一行"），再执行 |
| 结果直接刷屏 | 工具调用有可视化流：`⏺ read(...)` / `✓ 12 行` |
| 改完不知道改了什么 | 写文件后给 diff（`+2 -1`） |
| Ctrl+C 直接退出 | Ctrl+C 只中断当前任务，回到提示符 |
| 每句话互不相干 | 第 2 句能引用第 1 句的结果 |

## B77 · `veripen chat`〔P0，2 人天〕

| 项 | 内容 |
|---|---|
| 目标 | 交互式会话：自然语言 → 计划 → 工具调用流 → 结果；支持 diff、状态栏、中断、上下文累计 |
| 判据 | A：`读一下 out/x.txt` 能解析成 `read` 工具并执行（不要求用户知道工具名）；B：执行前先显示计划，且计划可被用户否决（`n` 不执行）；C：写/改文件后显示 diff 行数与摘要；D：`Ctrl+C` 只中断当前轮，回到提示符且会话不丢；E：启动时明确渲染决策层（`model: <名>` 或 `model: rules`），**不假装有模型**；F：第 2 轮能引用第 1 轮的结果（上下文累计） |
| 产出 | `veripen_cli/cmd_chat.py`（界面）+ `veripen_core/agent/intent.py`（规则意图解析）+ `tests/unit/test_chat_intent.py` + `tests/integration/test_chat_session.py` |
| 依赖 | `agent/session.py`（B67 会话与事件）、`workbench/fs.py`、`workbench/executor.py`、`agent/client.py`（模型可选） |
| PR | `b77/chat` |
| 回滚 | 删掉 `cmd_chat.py` 与 `intent.py`；`agent`（B67）保持不变 |

### 决策层必须可插拔

**本机现在没有任何模型后端**（`~/.litellm` 不存在，端口 4000 不通，
`VERIPEN_MODEL_*` 未配置）。所以：

```
配了 VERIPEN_MODEL_BASE_URL + VERIPEN_MODEL  → HttpChatClient（真模型）
没配                                        → 规则意图解析（intent.py）
```

**同一套界面**。启动横幅如实写当前用的是哪个 —— 这条与 `agent`（B67）
"未接模型就明说"是同一条纪律，不能因为界面变好看了就偷偷假装。

规则意图解析不是"降级糊弄"：它把常见说法映射到工具序列，
覆盖"读/写/改/跑命令/看状态"五类，且**说错时要给出它理解成了什么**，
让用户能纠正，而不是静默做错事。

### 界面形态

行模式（不引 textual/rich，与 B56 同一条零依赖纪律）：

```
HOS-ARES chat · model: rules · 工作根 <root>
> 看下 out/demo/x.txt 里写了什么

⏺ 计划
  1. read out/demo/x.txt
  执行？[Y/n]

⏺ read(out/demo/x.txt)
  ✓ 12 行 · 214B

  这是 demo 的输出……
```

写文件时多一段：

```
⏺ write(out/demo/y.txt)
  ✓ 写入 5B
  +hello
  -（新文件）
```

### 与 agent（B67）的关系

| | `veripen agent` | `veripen chat` |
|---|---|---|
| 输入 | 白名单动词 | 自然语言 |
| 计划 | 无 | 有，且可否决 |
| 定位 | 手工操作台（给知道工具的人） | 对话界面（给知道目标的人） |

两者共用 `Session`（事件落 `task_events`）与工作台工具，
**不重复实现状态**。`agent` 保留：脚本化场景需要确定性的动词入口。

### 与三支柱的关系

- **codex 面**：这就是它的入口；
- **strix 面**：chat 能调 `run` 的链路（"把这个目标跑一遍"），把自动化渗透塞进对话；
- **驭智面**：chat 能切到档（"按 demo-a 跑"），把企业编排塞进对话。

所以本批不是"又一个命令"，是把三个入口收到一个对话面板下。

## 执行记录（2026-11-05）

实测（`out/chattest/`，`--yes` 跳过确认）：

```
> 往 out/chattest/y.txt 写 hello
⏺ 计划  写 out/chattest/y.txt（5 字符）
  1. write(path=out/chattest/y.txt, content=hello)  — 写文件（新建/覆盖）
⏺ write(...)
  ✓ 写入 5B  +1 -0
  +hello

> 把 out/chattest/y.txt 里的 hello 改成 bye
⏺ 计划  把 out/chattest/y.txt 里的「hello」换成「bye」
⏺ edit(...)
  ✓ 替换 1 处  +1 -1
  -hello
  +bye

> 执行 python -V
  ✓ exit=0
  Python 3.13.12
```

判据 A–F 全绿：`test_chat_intent.py` 10 passed + `test_chat_session.py` 6 passed。

**期间修的四个真 bug**（都不是笔误，是设计与环境的边界）：

1. **`setup_stdout()` 只切 stdout，没切 stdin** —— 管道进来的中文按 GBK 解码，
   「写」变成乱码，只剩 ASCII 路径能认出来，于是**每句话都被当成"读文件"**。
   加 `setup_stdio()`（stdin+stdout 都切），并让 `_reconfigure` 对
   `io.StringIO` 兜底（否则测试必崩）。
2. **`sess.record` 遇孤立代理项直接抛**（`surrogates not allowed`）——
   一条工具输出的乱码能把整个会话打崩。加 `_scrub()` 在写库前 replace。
3. **写意图只按"含'写'字"判断** —— 「看下 x.txt 里写了什么」被判成 write。
   改成三条句式正则（往 X 写 Y / 写 Y 到 X / 创建 X 内容 Y）。
4. **Windows 下 `cmd -lc` 不存在** —— `执行 python -V` 永远 exit=1。
   执行器加 `cmd /c` 分支。

## 验收命令

```bash
python -m pytest tests/unit/test_chat_intent.py -q
python -m pytest tests/integration/test_chat_session.py -q
printf '读一下 out/demo/x.txt\nquit\n' | python -m veripen_cli.cmd_chat --root .
```

## 修订记录

| 日期 | 版本 | 变更 | PR |
|---|---|---|---|
| 2026-11-05 | `plan-hos-v1.0`（附录册） | 立册：B77 交互式 agent 界面 | 待提 |
