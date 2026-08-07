# Checklist

- [x] 应用名与顶栏/侧边栏品牌区显示 "HOS ARES"，ARES 在 UI 中清晰可见
- [x] 构建后 aapt2 验证 `app_name="HOS ARES"` 与字标资源存在
- [x] SettingsStore 默认后端为 deepseek、默认模型为 deepseek-chat，含 deepseekKey 字段
- [x] 设置界面含 DeepSeek API Key 输入框与"一键填入 DeepSeek 默认配置"按钮，后端下拉含 deepseek 项
- [x] 保存后 envMap 注入 `DEEPSEEK_API_KEY2`、`HOS_BACKEND=deepseek`、`HOS_MODEL`（默认 deepseek-chat）
- [x] 目录输入框旁有"选择目录"按钮，可打开系统文件夹选择器
- [x] 选择目录后回填推导路径（`primary:<子路径>` → `/sdcard/<子路径>`）并持久化访问权限；非主存储提示手动输入
- [x] 任务执行展示逐 Agent 状态卡片（pending/running/done/failed）与顶栏进行中状态
- [x] 点击 Agent 卡片弹出详情弹窗，展示识别技能与该 Agent 的流式详细输出（状态标签、等宽输出块、分隔）
- [x] APK 重新打包成功，品牌/配置/图标资源存在，运行资产（proot / alpine-minirootfs / opt/agents / run.sh / bootstrap）完整
