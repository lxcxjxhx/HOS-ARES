# Checklist

- [x] SettingsStore 含默认目录基础路径 `/sdcard/.ares/project`，并能生成带随机数/命名的唯一子目录路径
- [x] 目录为空时点击执行，自动回填 `/sdcard/.ares/project/<随机数>` 而非阻断
- [x] 仅输入任务、未填目录时，自动落在 `/sdcard/.ares/project/` 下
- [x] 回填后仍按目录去重创建/复用任务卡并正常执行
- [x] 移除了"请先填写工作目录"的阻断提示，仅保留任务必填校验
- [x] `:app:compileDebugKotlin` 编译通过
