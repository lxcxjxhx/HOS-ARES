# 13 · Phase 4：炫酷 UI 与配套图标设计（Ares-V3 Neon 视觉规范）

> 版本：v3.0-neon · 日期：2026-08-25 · 关联：10-Phase4-端到端链路接通、09-Phase3-AresGateway验收
> 目标：把 HOS-ARES 的 Android 端从"骨架灰"升级为 **赛博霓虹安全终端** 视觉，
> 与产品定位（手机上的渗透测试作战室）强一致，同时保证 APK 内零外部字体/图片依赖。

## 13.1 设计核心（一页版）

| 维度 | 决策 | 理由 |
|------|------|------|
| 基色 | 深紫黑 `#0B0710`（近黑紫） | 终端作战室氛围；OLED 省电；突出霓虹 |
| 主色 | 电光紫 `#A855F7`（primary） | 品牌色（安全风信子），深底上显眼 |
| 副色 | 霓虹粉紫 `#D946EF`（secondary） | 工具/强化动作 |
| 点缀 | 电光青 `#22D3EE`（tertiary） | 状态/数据/扫描线；与紫互补 |
| 语义色 | 完成=青、排队=灰、失败/错误=`#F87171` | 一眼区分状态 |
| 字体 | 标题 SansSerif 加粗 + 内容 Monospace（终端感）+ SansSerif 正文 | 零外部字体，系统栈即可 |
| 形态 | 玻璃卡片（半透明 surface + 渐变描边）、圆角 20dp | Compose 原生 Brush 实现 |
| 动效 | 扫描线循环、状态呼吸灯、执行中脉冲 | rememberInfiniteTransition，零依赖 |

**一句话**：深紫黑底 + 电光紫/青霓虹 + 玻璃卡片 + 扫描线——"移动渗透作战室的终端 HUD"。

## 13.2 设计令牌（Design Tokens）

### 颜色（theme/Color.kt 落地）

| Token | 值 | 用途 |
|-------|-----|------|
| `bg_ink` | `#0B0710` | 全屏底 |
| `bg_slate` | `#141020` | 次级面板底 |
| `surface_glass` | `#1C1530`（~66% 不透明视错觉） | 卡片 |
| `neon_purple` | `#A855F7` | primary / 标题渐变起点 |
| `neon_magenta` | `#D946EF` | secondary / 渐变中段 |
| `neon_cyan` | `#22D3EE` | tertiary / 扫描线 / 缓存高亮 |
| `pink_tint` | `#F472B6` | 工具行 `❯` 前缀 |
| `error_red` | `#F87171` | 失败 / 错误 |
| `text_hi` | `#F5EFFF` | 主文本 |
| `text_lo` | `#9E93B8` | 次要文本（推理区） |
| `text_dim` | `#6B6284` | 辅助/成本行 |

渐变：
- `grad_title` = linear(紫→粉)（标题）
- `grad_brand` = linear(紫@0, 粉@0.5, 青@1)（主按钮/品牌点）
- `grad_ring` = radial(青@0 → 透明)（状态光晕）

### 字体（theme/Type.kt）

- `displaySmall`/`titleLarge`：SansSerif Black/ExtraBold（标题）
- `bodyLarge/Medium`：Monospace（任务内容、工具输出——终端感）
- `labelSmall/Large`：SansSerif Medium（徽章、成本、按钮）

### 形态与间距

- 卡片圆角 `20dp`；输入框圆角 `16dp`；徽章 `pill`（圆角 999dp）
- 页面 padding `16dp`；卡片间距 `12dp`；输入区底部固定
- 状态点：直径 `8dp` 发光圆点；扫描线 高 `2dp` 全宽

## 13.3 界面布局（AresHomeScreen 重写）

```
┌────────────────────────────────────────────┐
│  ● HOS-ARES       [扫描线区]  [缓存命中率]  │  ← 渐变标题+呼吸logo点
│  移动渗透作战室 · reasonix Agent   ▼ 状态点  │
├────────────────────────────────────────────┤
│  ░░░ 扫描线（无限循环上下扫过卡片区）░░░    │
│  ┌─ 任务卡（玻璃 · 渐变描边）──────────┐   │
│  │ ●执行中 [skillId]         [取消]      │   │
│  │ 输入：对 sample.apk 做静态分析        │   │
│  │ 思考：先 apk_identify 识别壳…         │   │
│  │ ❯ mcp__mobile-security__apk_identify │  │ ← 粉色终端前缀
│  │   {"apk_path":"/tmp/x.apk"}           │  │
│  │ ██ 完成（青色）                      │  │
│  │ tokens 27k+200 · 缓存 99.5% · ¥0.022 │  │ ← 缓存≥90% 青色高亮
│  └───────────────────────────────────────┘ │
├────────────────────────────────────────────┤
│ [ 输入：对 sample.apk 做静态分析          ] │ ← 霓虹输入框（聚焦变青）
│ [                     ▶ 执行（渐变按钮）] │
└────────────────────────────────────────────┘
```

状态徽章：`● 排队中(灰) / ● 执行中(紫·脉冲) / ⚙ 调工具(粉·旋转感) / ✓ 完成(青·常亮) / ✕ 失败(红) / ○ 已取消(暗)`

## 13.4 App 图标（Adaptive Icon 规范）

- **背景**：`#0B0710` 深紫黑（`ic_launcher_background`）
- **前景**（`ic_launcher_foreground.xml`，108dp 画布，图标居安全区 66dp）：
  - 外环：电光紫描边圆环（扫描雷达）
  - 右上卫星点：霓虹粉
  - 中央：终端提示符 `>_`（粉色折线 + 青色下划线 + 青色光标块）
  - 底部三点：数据节点（紫/粉/青，呼吸感）
- 语义：**盾环 + 终端提示符 = 安全 + 智能 Agent；扫入感 = 审计/分析**
- 配套：`ic_launcher.xml` + `ic_launcher_round.xml`（adaptive，minSdk 26 起原生支持）
- Manifest：`android:icon` / `android:roundIcon` 指向 `@mipmap/ic_launcher(_round)`

## 13.5 实现清单（本轮落盘）

| 文件 | 内容 |
|------|------|
| `android/app/src/main/java/com/hos/ares/ui/theme/Color.kt` | 令牌色 + 渐变 Brush 常量 |
| `android/app/src/main/java/com/hos/ares/ui/theme/Type.kt` | 字体栈 |
| `android/app/src/main/java/com/hos/ares/ui/theme/Theme.kt` | HosAresTheme（强制暗色） |
| `android/app/src/main/java/com/hos/ares/ui/AresHomeScreen.kt` | 炫酷版重写（玻璃卡/扫描线/呼吸点/渐变） |
| `android/app/src/main/java/com/hos/ares/MainActivity.kt` | 接入 HosAresTheme + enableEdgeToEdge 沉浸 |
| `android/app/src/main/res/values/themes.xml` | 系统主题改暗色（windowBackground/状态栏） |
| `android/app/src/main/res/mipmap-anydpi-v26/ic_launcher.xml` | adaptive icon |
| `android/app/src/main/res/mipmap-anydpi-v26/ic_launcher_round.xml` | adaptive round icon |
| `android/app/src/main/res/drawable/ic_launcher_foreground.xml` | 霓虹矢量前景 |
| `android/app/src/main/res/values/ic_launcher_background.xml` | `#0B0710` |
| `android/app/src/main/AndroidManifest.xml` | 补 icon/roundIcon |

## 13.6 验收要点

- **零外部依赖**：无字体文件、无图片资源、无新 gradle 依赖（仅 Compose BOM 既有 API）
- 暗色强制：状态栏/导航栏 `#0B0710`，App 内无白闪
- 图标：adaptive 矢量，任意 mipmap 密度均清晰（矢量缩放）
- 与 Phase 4 链路兼容：VM/cards/submit/cancel 接口不变，仅视觉重写
- CI 冒烟不回归：图标/主题不影响 serve 启动与 SSE 协议

---

*后续交付：UI 代码与图标随 PR 合入 main，CI build-apk 产出含新视觉的 APK。*