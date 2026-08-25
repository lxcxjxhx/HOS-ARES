# 12 · Phase 5 前置：MCP 工具调用级兼容探测与适配层落地（实测闭环）

> 实测日期：2026-08-25 · reasonix v1.19.1 · mobile-security-mcp 0.1.4 · 影响结论：
> **08-Phase2 的"mcp==1.28.1 锁定"描述修正为"mcp==1.28.1 安装 + 适配层网关接入"。**

## 12.1 现象：握手/发现通过，但工具调用始终错位

reasonix 会话内真实调用 `mcp__mobile-security__apk_identify`：

```
tool_dispatch: {"name":"mcp__mobile-security__apk_identify","args":{"apk_path":"/tmp/nonexistent.apk"}}
tool_result:   err: "create_server.<locals>.call_tool() takes 1 positional argument but 2 were given"
```

- 链路层 ✅：LLM 产生调用 → dispatch → MCP 服务器收到并回错误 → Agent 收尾。
  跨会话前缀缓存命中 55168/55423 ≈ **99.5%**（再次实证缓存优先卖点）。
- 服务器实现层 ❌：handler 与 mcp-SDK 派发签名错位。

## 12.2 根因（源码级证据，server.py:1006-1007）

```python
@server.call_tool()                       # mcp-SDK 装饰器
async def call_tool(request: CallToolRequest) -> CallToolResult:   # ← 单参整请求
```

包内 handler 采用 **mcp-SDK ≤0.x/1.0 世代**的单参签名 `call_tool(request)`；
而 mcp-SDK 0.9.1 / 1.9.3 / 1.28.1 的派发层均以 `(name, arguments)` 双参调用 →
**三个世代全部报同一错误**。同时 `list_tools()` 在 0.x/1.9.x 下返回形状校验失败
（仅 1.28.1 的 tools/list 通过，reasonix 曾列出 54 工具）。

**结论：继续二分 SDK 版本无意义（非版本噪声，是签名世代错位）。**

## 12.3 修复：mcp-compat-gw.py 适配层（方案既定兜底路线的落地）

实现（`tools/mcp-compat-gw.py`）：**完全不依赖 mcp-SDK 的 Server/装饰器机制**，
自行实现 MCP stdio 线协议（JSON-RPC over stdin/stdout，逐行帧），直接 import：

- `tools/list` ← `from mobile_security_mcp.server import TOOL_SCHEMAS`（54 项纯 dict 直出）
- `tools/call`  ← 按工具名 `getattr(handler, name)` 直调对应 handler 方法
  （dispatch 表与 server.py 的 `_dispatch` 一致：Static/Dynamic/Rasp/Device/Network/
  Signing/Memory/Native/Flutter/Patcher 十大类 + check_tools/install_tool 两 setup 工具）
- 支持 sync/async 方法自动识别；错误统一包装为 MCP CallToolResult。

## 12.4 验证（全链路，实测通过）

| 探针 | 结果 |
|------|------|
| 裸协议 4 帧（initialize→initialized→list→call apk_identify） | ✅ 54 工具 schema 正常返回；call 真实派发到 handler，返回 `{"status":"missing_tool","tool":"apkid"…}`（本机未装 apkid 二进制，符合预期） |
| reasonix 真实会话 E2E（调用 check_tools） | ✅ `mcp__mobile-security__check_tools` 派发成功，返回 37 项工具依赖检查 JSON（apkid/apkleaks/androguard/quark可用，frida/objection 待装），Agent 回复 "OK"；成本 ¥0.0224，累计缓存命中占 91%+ |

**证据链完整：握手 → 发现 54 工具 → 工具调用派发 → Agent 消费结果，全部在 reasonix 内闭环。**

## 12.5 对方案文档的影响（需回写）

| 文档 | 原表述 | 修正 |
|------|--------|------|
| `08-Phase2-MCP工具链验收.md` | "锁定 mcp==1.28.1" | mcp==1.28.1 **仅作为包运行底座**，工具调用必须经 `mcp-compat-gw.py` 接入 |
| `11-Phase5-APK全量打包方案.md` | 冒烟只验 ROOTFS-OK/健康检查 | 冒烟追加**调用级断言**：reasonix 会话内发起 check_tools，断言 tool_dispatch/tool_result 帧存在 |
| `scripts/build-rootfs.sh` | 直接 `pip --target /opt/ares-libs` | 烘烤镜像内把 `mcp-compat-gw.py` 拷入 `/root/hos-ares/tools/`，`HOS_ARES_PYTHONPATH=/opt/ares-libs`，插件配置指向网关（已改） |
| `reasonix.toml`（根+模板） | `command=python args=["-m","mobile_security_mcp"]` | `args=["tools/mcp-compat-gw.py"]`，`PYTHONPATH=${HOS_ARES_PYTHONPATH:-tools/python-libs-compat}`（已改） |

## 12.6 回写状态

- ✅ tools/mcp-compat-gw.py（v3 最终版，含三帧验证注释）
- ✅ reasonix.toml（根 + config/ 模板）插件已切网关
- ✅ scripts/build-rootfs.sh 已更新（网关入库 + env）
- 🔄 08/11 文待回写（下一轮）
- ✅ 12 文本文件（本轮）

---

*关联：`08-Phase2-MCP工具链验收.md`（底座版本回写）、`11-Phase5-APK全量打包方案.md`（冒烟断言升级）、
`10-Phase4-端到端链路接通.md`（serve 链路基线）*