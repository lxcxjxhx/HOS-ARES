# 终端体验录屏（B69）

一条命令产出三支柱的**执行过程**视频：

```bash
python scripts/demo/record_terminal.py
```

产物在 `out/demo/terminal/`：

| 文件 | 用途 |
|---|---|
| `terminal-tour.mp4` | 1280×720 / 15.1s / 三幕 |
| `frames/` | 42 张逐帧 PNG（每幕 14 帧） |
| `manifest.json` | 场景、行数、关键行 —— **机器可判的那份** |

## 三幕

| 幕 | 命令 | 画面里能看到 |
|---|---|---|
| 1 `run` | `cmd_run --target … --openapi …` | 路由数 / 逐 oracle 判定 / `VERIFIED 2` / capsule 数 |
| 2 `agent` | `cmd_agent --db …` | `ok write` → `ok read`，末行"最近一步" |
| 3 `platform` | `cmd_platform --targets a,b --require-approval --approve` | 审批 APPROVED / 两资产进度 / `汇总：2 资产` |

![run 幕](../out/demo/terminal/frames/013-run-12.png)
![platform 幕](../out/demo/terminal/frames/040-platform-11.png)

> 图片路径相对仓库根；若渲染器不解析，直接打开 `out/demo/terminal/frames/`。

## 画面里的东西是真的吗

是。每一行都是**真命令的 stdout**：

- 靶场由脚本起（`LabServer`，两个实例模拟两个资产）
- 三条命令通过 `subprocess` 真跑，捕获 stdout
- 行号可回溯到 `manifest.json` 的 `all_lines`

**取数方式**：捕获 stdout → 渲染终端 HTML → 逐帧截图 → ffmpeg。
不引入 asciinema（新依赖），也不录桌面（不可复现）。

代价：没有打字动画与光标闪烁。取舍是有意的——**内容是证据，动画不是**。

## 判据（H14 B69）

| # | 判据 | 怎么验 |
|---|---|---|
| A | ≥12s、1280×720、H.264 | `validate_manifest` + `ffprobe` |
| B | 三条命令串都在画面里 | `manifest.scenes[].prompt` |
| C | 关键结果可见（`VERIFIED 2` / `ok  write` / `汇总：2 资产`） | `manifest.all_lines` |
| D | 两次运行场景序列与行数一致 | 手动跑两次比对（不要求视频字节一致） |
| E | 只连 `127.0.0.1` | 靶场由 `LabServer` 起在环回 |

实测：`场景 ['run','agent','platform'] / 51 行 / 42 帧 / 15.07s`。

## 期间踩到的三个坑（都写进了代码注释）

1. **精简 env 让 socket 初始化失败**（`WinError 10106`）
   subprocess 只传 `PATH`/`PYTHONPATH` 时，Windows 缺 `SystemRoot` 等变量 →
   Winsock 起不来 → 靶场连不上 → 所有 oracle 报 OSError。
   修：`env = dict(os.environ)` 再覆盖。

2. **管道下默认编码是 locale（GBK）**
   `PYTHONIOENCODING` 不够，中文解成乱码，判据里的关键行永远匹配不上。
   修：加 `PYTHONUTF8=1` 强制 UTF-8 模式。

3. **命令串在 prompt 里、不在 stdout 里**
   去掉重复的 `$ …` 行后，判据 B 一度假红。修：把 `scenes[].prompt` 纳入校验 blob。

## 与 B65 的关系

- `B65` 录**控制台 10 页**（产品面，看界面）
- `B69` 录**三条命令的执行过程**（三支柱，看能力）

产物独立、不合并——合并会让"哪一段是哪批的证据"变模糊。
