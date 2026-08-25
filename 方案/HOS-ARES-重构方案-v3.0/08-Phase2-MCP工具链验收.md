# 08 · Phase 2 验收：MCP 安全工具链（开发机实测，2026-08-25）

## 8.1 验收结论 ✅

| 验收项 | 结果 | 证据 |
|--------|------|------|
| reasonix 注册/发现 MCP 服务器 | ✅ | `reasonix mcp list` 显示 5 个服务器（codegraph + hos-ares-demo + mobile-security + mcp-termux + jadx-headless） |
| reasonix MCP 客户端调用链路（LLM→工具） | ✅ | `-p` 会话中 `tool_dispatch: mcp__hos-ares-demo__demo_add {"a":5,"b":7}` → `tool_result: demo_add(5,7)=12` → 结果 `12`（¥0.0013/次） |
| 缓存复用（cache-first 实证） | ✅ | 第二次会话 `cacheHitTokens=22400/22513`（≈99.5% 前缀命中），跨会话复用生效 |
| mobile-security-mcp 可运行 | ✅ | 握手成功（protocolVersion 2025-11-25），`tools/list` 返回 **54 个安全工具** |

## 8.2 mobile-security-mcp 实测要点（重要经验）

- **版本兼容**：`mobile_security_mcp 0.1.4` 依赖 `mcp>=1.0.0`，但 **mcp SDK 2.x 已移除 `Server.list_tools` 装饰器，运行时崩溃**（`AttributeError`）。实测 **mcp==1.28.1** 作为包运行底座正常。
- **⚠ 工具调用级兼容（12 文实测闭环）**：包内 handler 为**单参签名** `call_tool(request)`（server.py:1007），mcp-SDK 0.9.1/1.9.3/1.28.1 三世代均以 `(name, arguments)` 双参派发 → 直连 `tools/call` 恒报 `takes 1 positional argument but 2 were given`。**并非 1.28.1 的缺陷，任何 SDK 版本都救不了**。因此工具调用一律经 `tools/mcp-compat-gw.py` 适配层（绕过 SDK 装饰器、直调 handler；54 工具清单直取自 `TOOL_SCHEMAS`），reasonix 会话内已实测派发成功、Agent 正常消费结果。
- **隔离安装**（沙箱约束、审批 never 模式下）：`python -m pip install --no-cache-dir --target tools/python-libs-compat "mcp==1.28.1" "mobile-security-mcp"`（全新目录完整安装，规避 force-reinstall 删坏 pydantic_core 的文件锁问题；根配置 `PYTHONPATH=${HOS_ARES_PYTHONPATH:-tools/python-libs-compat}`）。
- **运行时环境**：`PYTHONPATH=${HOS_ARES_PYTHONPATH:-tools/python-libs-compat}`（相对=以工作目录解析；Android 容器端建议绝对路径 `HOS_ARES_PYTHONPATH=/opt/ares-libs`，与 11 文 rootfs 烘烤段一致）。
- 工具清单 54 项覆盖方案 §4.1 全部能力：

| 能力域 | 代表工具 |
|--------|---------|
| 静态分析 | apk_decompile(apktool)、apk_decompile_java(jadx)、apk_identify(apkid)、apk_scan_secrets(apkleaks)、apk_analyze_full、manifest_parse、search_strings |
| 动态插桩 | frida_spawn/attach/inject/read_output/detach/memdump/find_bytes、frida_server_push、objection_run、frida_spawn_hluda（反 RASP 扫描） |
| RASP | rasp_identify、rasp_bypass、ssl_kill_switch |
| 跨平台 | hermes_decode(RN)、blutter_extract + dart_snapshot_info(Flutter)、il2cpp_dump(Unity)、native_strings/exports/disasm(radare2) |
| 设备控制 | adb_devices/shell/install/pull/push/logcat、scrcpy_start/screenshot |
| 网络 | mitm_start/stop/read_flows/export_har |
| 集成分析 | analyze_code(androguard)、find_crypto_usage、find_urls、smali_patch、inject_gadget、apk_rebuild_sign、apk_sign |
| 会话记忆 | memory_read/memory_write（per-target） |
| 运维 | check_tools、install_tool |

## 8.3 偏差记录（写入 config/reasonix.toml 的依据）

- 方案配置中的 `mcpServers`(JSON) → 实测 `[[plugins]]`(TOML)：`command`/`args`/`env`（`${VAR}` 展开），stdio 默认，远程用 `--http/--sse`。
- `mcp-termux`、`jadx-headless-mcp`、`droid-mcp` 为 **Termux/ARM 目标**，开发机（Windows）不可运行 → 状态显示为注册但未连接；Android 部署后经 `scripts/setup-termux.sh` 安装并复验。
- 注册入口二选一：编辑 `./reasonix.toml` 直接写 `[[plugins]]`，或 `reasonix mcp add <name> -- <command> [args...]`（项目级将写入 ./reasonix.toml）。

## 8.4 下一步（Phase 3 已并行开始）

- AresGateway Kotlin 骨架已落盘（`ares-gateway/src/main/kotlin/com/hos/ares/gateway/`：TaskCard / ReasonixEvent / Skill / SkillRegistry / ReasonixClient / HttpStreams / ProcessStreams / AresGateway）。
- Phase 4 前需完成：SSE 传输真实实现（OkHttp-sse）、Gradle 编译验证（开发机无 Gradle 时延后到 Phase 5 打包）。