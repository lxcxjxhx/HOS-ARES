# HOS-ARES UI 交互与品牌完善 Spec

## Why
当前 HOS-ARES APK 已能在手机端通过 proot 运行安全 Agent，但存在四方面体验短板，影响"开箱即用"：
1. HOS 为品牌名、ARES 为产品/Agent 平台名，但 ARES 完全未在 UI 呈现；
2. API 配置界面缺少 DeepSeek（reasonix 正是基于 DeepSeek 优化），默认后端也不符合预期，配置不够便捷；
3. 工作目录只能手动输入，缺少图形化的本地目录选择；
4. 任务执行输出为单个大文本，缺乏 Codex/Claude 式的结构化交互，reasonix 原有的详细数据与弹窗展示丢失。

本 Spec 在不改运行核心（proot / Alpine / Agent）的前提下，聚焦 Android UI 层的品牌、配置、目录选择与交互体验升级。

## What Changes
- **品牌体现**：HOS 为品牌名、ARES 为产品/Agent 平台名，二者在应用名、顶栏字标、侧边栏品牌区一并呈现。
- **便捷 API 配置**：新增 DeepSeek 后端并设为默认；增加 DeepSeek API Key 字段与推荐默认模型；提供"一键填入 DeepSeek 默认配置"的便捷操作。
- **目录图形化选择**：工作目录旁增加"选择目录"按钮，通过系统文件夹选择器（SAF `OpenDocumentTree`）图形化选择本地目录，回填为 `/sdcard/...` 路径并持久化访问权限，保留手动输入作为兜底。
- **Codex/Claude 式交互**：将单一大文本输出升级为结构化任务详情 —— 顶栏状态 + 每个 Agent 的运行状态卡片（pending/running/done/failed）+ 点击弹出的详情弹窗，展示该任务识别出的技能、逐 Agent 状态与流式详细输出，恢复 reasonix 式详细数据展示。

## Impact
- **品牌资源**：`res/values/strings.xml`（应用名/字标）、`res/layout/activity_main.xml`（顶栏、抽屉品牌区）。
- **配置**：`SettingsStore.kt`（新增 deepseekKey、默认后端/模型、envMap 注入）、`SettingsActivity.kt`、`res/layout/activity_settings.xml`。
- **目录选择**：`MainActivity.kt`（SAF 选择器 + 路径推导 + 权限持久化）、`res/layout/activity_main.xml`（目录行新增按钮）、可能新增依赖 `androidx.activity:activity-ktx`。
- **交互**：`ReasonixGateway.kt`（结构化事件流）、`MainActivity.kt`（状态卡片 + 详情弹窗 + 格式化输出）、新增任务详情布局与适配器。
- 不影响 proot/Alpine/Agent 运行核心；现有任务卡（目录去重）逻辑保持。

## ADDED Requirements

### Requirement: ARES 品牌体现
应用 UI 须同时呈现 HOS（品牌名）与 ARES（产品/Agent 平台名）两层身份，而非只显示 HOS。

#### Scenario: 成功展示品牌
- **WHEN** 安装并打开应用
- **THEN** 桌面启动器标签显示 "HOS ARES"；主界面顶栏与侧边栏品牌区均显示 "HOS ARES" 字标（ARES 清晰可见），副标题仍保留安全/Agent 定位

### Requirement: 便捷 API 配置与 DeepSeek 默认
配置界面须支持 DeepSeek 并默认选中；提供 DeepSeek API Key 字段、推荐默认模型，并支持一键填入 DeepSeek 推荐配置，使多数用户零认知即可完成配置。

#### Scenario: DeepSeek 默认与一键配置
- **WHEN** 用户打开设置界面
- **THEN** 后端默认选中 "deepseek"，出现 DeepSeek API Key 输入框，且提供"一键填入 DeepSeek 默认配置"按钮（自动填入默认模型，如 deepseek-chat）
- **AND** 保存后运行 Agent 时，`envMap()` 将 DeepSeek Key 注入为 `DEEPSEEK_API_KEY2`，并设置 `HOS_BACKEND=deepseek`、`HOS_MODEL`（默认 deepseek-chat）

#### Scenario: 兼容既有后端
- **WHEN** 用户选择 claude/openai/gemini/local
- **THEN** 原有配置与 envMap 注入逻辑保持可用，不被 DeepSeek 改动破坏

### Requirement: 本地目录图形化选择
工作目录须支持图形化选择：目录输入框旁提供"选择目录"按钮，调用系统文件夹选择器选择本地目录，回填为 Agent 可访问的真实路径，并持久化选择权限。

#### Scenario: 选择目录
- **WHEN** 用户点击"选择目录"按钮
- **THEN** 弹出系统文件夹选择器（SAF），用户图形化选定目录后，输入框自动回填推导出的路径（`primary:<子路径>` → `/sdcard/<子路径>`），并调用 `takePersistableUriPermission` 持久化访问权限

#### Scenario: 非主存储兜底
- **WHEN** 所选目录来自非主存储（无法推导为 `/sdcard` 路径）
- **THEN** 保留手动输入可用，并提示用户可手动填写路径

### Requirement: Codex/Claude 风格交互与详情弹窗
任务执行须展示结构化、可交互的 Agent 运行结果：顶栏状态、每个 Agent 的状态卡片，以及点击卡片弹出的详情弹窗，展示该任务的详细流式输出；整体视觉风格参考 Codex/Claude 的对话/控制台形态，恢复 reasonix 式的详细数据呈现。

#### Scenario: 结构化执行与详情弹窗
- **WHEN** 用户执行任务且多个 Agent 被调度
- **THEN** 主界面展示逐 Agent 状态卡片（pending/running/成功/失败），执行中顶栏显示进行中状态
- **AND** 用户点击某 Agent 卡片，弹出详情弹窗，展示该任务识别到的技能、该 Agent 的流式详细输出（含基础格式化：状态标签、等宽输出块、分隔），数据随执行实时更新

## MODIFIED Requirements
（无——本 Spec 为新增 UI 能力，不改动现有任务卡去重、proot 运行等既有需求。）

## REMOVED Requirements
（无。）
