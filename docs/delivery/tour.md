# 成片（B70）

一条命令出片：

```bash
make demo-record               # 录制 + 合成（B239：`make demo` 是端到端演示，不是成片）
python scripts/demo/build_tour.py   # 素材已在时只合成
```

产物：

| 文件 | 内容 |
|---|---|
| `out/demo/hos-ares-tour.mp4` | **成片** 1280×720 / 62.9s / 6 段 |
| `out/demo/cards/*.png` | 片头 / 章节 / 片尾卡片 |
| `out/demo/tour-manifest.json` | 段落表 + 预期时长（机器可判） |

## 段落结构

| # | 段 | 时长 | 内容 |
|---|---|---|---|
| 1 | `intro` | 2.5s | HOS-ARES · 三支柱 |
| 2 | `ch-console` | 1.5s | 01 · 控制台（11 个只读页） |
| 3 | `console` | 30.03s | B65 素材：真浏览器走 11 页 |
| 4 | `ch-terminal` | 1.5s | 02 · 五幕终端 |
| 5 | `terminal` | 24.87s | B69 素材：run / agent / platform / profile / chat（五幕） |
| 6 | `outro` | 2.5s | 复现命令 + 证据位置 |

六段之和 = 62.90s，成片实测 62.9s（判据 C 要求误差 <1.5s）。（B270 更新：控制台与终端两段素材分别长到 30.03s / 24.87s，旧文的总长已经对不上，判据 C 按旧数字其实是过不了的。）

![片头](out/demo/cards/intro.png)

## 判据（H15 B70）

| # | 判据 | 结果 |
|---|---|---|
| A | ≥40s、1280×720、H.264 | ✅ 62.9s |
| B | 片头 + 3 张章节卡都在 | ✅ 6 段齐全 |
| C | 成片 ≈ 各段之和（<1.5s） | ✅ 62.90 vs 62.90 |
| D | `make demo-record` 可重跑出同样结构 | ✅ 段落序列固定 |
| E | 只连环回，不下载素材 | ✅ 素材全本地生成 |

```
pytest tests/unit/test_tour_build.py -q  → 7 passed
```

## 实现里踩的坑

**卡片 PNG 与 mp4 混进同一个 concat 会花屏。**
`-loop 1` 必须配 `-framerate`，且 `-t` 要放在 `-i` **之后**（输出时长）——
放在输入前会被解释成"读输入多久"，单图输入直接报错退出。

所以流程是：卡片 → 各自转成同参数 mp4（1280×720/30fps/yuv420p）→ 与素材一起进 concat。

## 三段产物的角色

| 批 | 产物 | 角色 |
|---|---|---|
| B65 | `console-tour.mp4` | 素材（界面） |
| B69 | `terminal-tour.mp4` | 素材（能力） |
| B70 | `hos-ares-tour.mp4` | **成片**（给人看的） |

素材是可引用的证据，成片是可传播的叙事。三段都保留。

## 前置条件

- `ffmpeg` / `ffprobe` 在 PATH
- `pip install playwright && playwright install chromium`
- 控制台录制需要 `uvicorn`（已在依赖里）

缺任一条件时 `build_tour.py` 会报出缺什么，而不是静默失败。
