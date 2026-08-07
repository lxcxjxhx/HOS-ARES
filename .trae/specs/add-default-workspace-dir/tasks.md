# Tasks

- [x] Task 1: 默认工作目录常量
  - [x] SubTask 1.1: `SettingsStore.kt` 新增默认目录常量/字段 `defaultProjectDir = "/sdcard/.ares/project"`，并支持通过方法生成带随机数/命名的默认子目录路径
  - [x] SubTask 1.2: 校验该方法返回的路径均以 `/sdcard/.ares/project/` 为前缀且唯一

- [x] Task 2: MainActivity 默认目录自动回填
  - [x] SubTask 2.1: 修改 `MainActivity.btnRun`：目录为空时自动生成默认路径（优先任务描述命名，否则随机数）并回填 `etDirectory`
  - [x] SubTask 2.2: 移除"请先填写工作目录"的阻断提示；仅保留任务文本必填校验
  - [x] SubTask 2.3: 保证回填后仍走 `TaskStore.getOrCreate` 目录去重与执行流程
  - [x] SubTask 2.4: 编译验证（`gradlew.bat :app:compileDebugKotlin --no-daemon`）

# Task Dependencies
- [Task 2] depends on [Task 1]（默认目录生成逻辑在 SettingsStore）
