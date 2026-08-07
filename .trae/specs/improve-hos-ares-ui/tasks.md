# Tasks

- [x] Task 1: ARES 品牌体现在 UI
  - [x] SubTask 1.1: 更新 `res/values/strings.xml`：`app_name` 改为 "HOS ARES"，新增 `brand_wordmark="HOS ARES"`（或保留 HOS + 增加 ARES 字标），并调整 `brand_subtitle` 定位文案
  - [x] SubTask 1.2: 更新 `res/layout/activity_main.xml` 顶栏与侧边栏品牌区，使 "HOS ARES" 字标清晰呈现（复用 `ic_logo`）
  - [ ] SubTask 1.3: 构建 APK 并通过 aapt2 验证 `app_name` 与字标资源内容

- [x] Task 2: 便捷 API 配置 + DeepSeek 默认
  - [x] SubTask 2.1: `SettingsStore.kt` 新增 `deepseekKey`；默认 `backend = "deepseek"`、默认 `model = "deepseek-chat"`；`envMap()` 在 deepseek 时注入 `DEEPSEEK_API_KEY2`，并始终设置 `HOS_BACKEND`、`HOS_MODEL`
  - [x] SubTask 2.2: `res/layout/activity_settings.xml` 增加 DeepSeek API Key 输入框与"一键填入 DeepSeek 默认配置"按钮；后端下拉新增 `deepseek` 项
  - [x] SubTask 2.3: `SettingsActivity.kt` 回填/保存 deepseekKey；实现一键预设（自动填入默认模型）；验证保存后 envMap 注入正确

- [x] Task 3: 本地目录图形化选择
  - [x] SubTask 3.1: 若需 SAF 支持，在 `app/build.gradle` 添加 `androidx.activity:activity-ktx` 依赖
  - [x] SubTask 3.2: `MainActivity.kt` 使用 `ActivityResultContracts.OpenDocumentTree` 打开文件夹选择器；`primary:<子路径>` → `/sdcard/<子路径>` 推导；`takePersistableUriPermission` 持久化权限；非主存储提示手动输入
  - [x] SubTask 3.3: `res/layout/activity_main.xml` 目录输入行旁新增"选择目录"按钮
  - [x] SubTask 3.4: 验证选择目录后输入框回填路径并持久化

- [x] Task 4: Codex/Claude 风格交互 + 详情弹窗
  - [x] SubTask 4.1: `ReasonixGateway.kt` 改为发布结构化执行事件（识别到的技能、逐 Agent 状态 pending/running/done/failed、流式详细文本），保留既有输出流
  - [x] SubTask 4.2: 新增任务详情弹窗布局与适配器（状态标签、等宽输出块、分隔线）
  - [x] SubTask 4.3: `MainActivity.kt` 渲染逐 Agent 状态卡片与顶栏进行中状态；点击卡片弹出详情弹窗，流式更新详细输出
  - [x] SubTask 4.4: 验证任务执行呈现结构化状态与详情弹窗、数据实时更新

- [x] Task 5: 重新打包与端到端验证
  - [x] SubTask 5.1: 运行 `build.ps1` 重新打包 APK（clean 后 assembleDebug）
  - [x] SubTask 5.2: aapt2 验证资源：`app_name="HOS ARES"`、deepseek 相关字符串与图标资源存在
  - [x] SubTask 5.3: 校验资产完整（proot / alpine-minirootfs / opt/agents / run.sh / bootstrap）

# Task Dependencies
- [Task 2] 独立（仅改动设置相关文件）
- [Task 3] depends on [Task 1]（两者均改动 `activity_main.xml`，需顺序避免冲突）
- [Task 4] depends on [Task 3]（两者均改动 `MainActivity.kt`）
- [Task 5] depends on [Task 1]~[Task 4]
