# 默认工作目录自动生成 Spec

## Why
当前新建任务时要求用户必须手动填写工作目录，否则提示"请先填写工作目录"并中断。对于未配置/未选择目录的用户，缺少一个开箱即用的默认工作区，导致仍要手动输入路径（体验割裂）。本变更在用户未指定目录时，自动采用默认项目路径 `/sdcard/.ares/project/`，并以随机数或用户命名生成子目录。

## What Changes
- 在 `MainActivity.btnRun` 中：当工作目录为空时，不再阻断，而是自动回填默认路径 `/sdcard/.ares/project/<项目名>`。
  - 项目名取值优先级：若用户输入了任务且任务文本形似项目名（非空），可选用；否则用随机数（如 `project-<时间戳/随机>`）兜底，保证唯一。
- 新增 `SettingsStore.defaultProjectDir` 常量/字段：基础默认目录 `/sdcard/.ares/project`，可被后续配置界面复用。
- 回填后仍走既有任务卡目录去重逻辑（`TaskStore.getOrCreate`），确保默认目录同样生成/复用任务卡。
- 在输入框为空、任务为空时：仅提示输入任务，不再因目录为空而拒绝执行。

## Impact
- Affected specs: improve-hos-ares-ui（任务执行/目录选择相关交互）
- Affected code:
  - `app/app/src/main/java/com/hos/ares/MainActivity.kt`（btnRun 逻辑）
  - `app/app/src/main/java/com/hos/ares/SettingsStore.kt`（新增默认目录常量）

## ADDED Requirements
### Requirement: 默认工作目录自动生成
系统在用户未配置/未选择工作目录时，自动采用 `/sdcard/.ares/project/<项目名>` 作为默认工作目录，其中项目名为随机数或用户命名。

#### Scenario: 未填目录时执行任务
- **WHEN** 用户未填写工作目录，点击"执行"
- **THEN** 应用自动回填 `/sdcard/.ares/project/<随机数>`，据此创建/复用任务卡并正常执行，不阻断

#### Scenario: 用户命名默认项目
- **WHEN** 用户仅输入任务描述而未填目录
- **THEN** 若任务描述可作为命名来源则使用之，否则以随机数兜底，均落在 `/sdcard/.ares/project/` 下

## MODIFIED Requirements
### Requirement: 任务执行目录校验
原逻辑要求目录非空否则中断。现改为：目录为空时自动回填默认目录，仅保留任务文本必填校验。

## REMOVED Requirements
### Requirement: 目录空则报错阻断
**Reason**: 破坏开箱即用体验，用户未配置时无法直接开始。
**Migration**: 以默认目录自动生成替代；用户仍可手动输入或图形化选择目录以覆盖默认值。
