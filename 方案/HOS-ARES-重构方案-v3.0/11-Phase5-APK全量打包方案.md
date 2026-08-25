# 11 · Phase 5：APK 全量打包方案（自包含装载，不依赖系统 Termux）

> 决策背景：原方案写"APK 内打包 Termux + proot-distro"，但 Termux 与 proot-distro 是**用户层组件**，正常 APK 无法"打包"它们本体。为确保"装完 APK 即含完整 Agent 框架 + MCP 工具链"，改为**自包含容器装载**：APK 内嵌预烘烤的 Alpine rootfs + proot 静态二进制，首启解压、应用进程内拉起 reasonix serve。偏差记录见 §11.5。

## 11.1 装载架构（APK 视角）

```
HOS-ARES.apk（目标体积 Tier 分级，见 §11.3）
├── classes.dex + resources          ← Compose UI + AresGateway（android/ 与 ares-gateway/）
├── lib/proot-arm64.so ①            ← proot 静态二进制（用户态，无需 root）
└── assets/rootfs.tar.xz ②          ← 预烘烤 Alpine rootfs（含 Node22 + reasonix + MCP 链）
         │  首启：RootfsInstaller 解压到 filesDir/rootfs
         ▼
filesDir/rootfs/  +  ReasonixServeBootstrap
    exec: proot -0 -r <rootfs> /bin/sh -c "cd /root/hos-ares && reasonix serve --addr 127.0.0.1:8931 --auth token --token <T>"
         ▼
127.0.0.1:8931（loopback 共享，App 直连）
    AresGateway(OkHttp-sse)：① GET /?token= 握 Cookie ② /events SSE ③ POST /submit   ← 10 文协议
```

①**proot 静态二进制（arm64）**：`https://github.com/termux/proot` 预构建版本（~1.5MB），作为 `lib/` 或 `assets/` 装载，用户态 chroot——**不需要手机已装 Termux，不需要 root**。
②**rootfs 烘烤**在 CI/容器中完成（`scripts/build-rootfs.sh`，docker alpine 基座），产出单一 `rootfs.tar.xz` 资产；APK 首启解压 + 首备路径自检。

## 11.2 首启流程与资源清单

| 步骤 | 内容 |
|------|------|
| 1 | `RootfsInstaller` 解压 `assets/rootfs.tar.xz` → `filesDir/rootfs`（显示进度，异常回滚重试） |
| 2 | 自检：`<rootfs>/usr/bin/node --version`、`<rootfs>/root/.npm-global/bin/reasonix --version`、MCP 模块存在性 |
| 3 | `ReasonixServeBootstrap` 启动后台进程：`proot -0 -r <rootfs> /bin/sh -c 'cd /root/hos-ares && REASONIX_HOME=<rootfs>/etc/reasonix reasonix serve --addr 127.0.0.1:8931 --auth token --token <T>'`（启动前经 `HOS_ARES_PYTHONPATH=/opt/ares-libs` 注入 MCP 库；`mobile-security` 插件经 `tools/mcp-compat-gw.py` 网关接入，见 12 文） |
| 4 | 健康检查：`GET /?token= <T>` → 200 → 通知 AresGateway 就绪（重试 ×N、超时告警） |
| 5 | 会话生命周期：Activity 退出置服务为 STICKY；多任务复用同一 serve 长会话（缓存友好） |

rootfs 内预置（烘烤阶段写入，首启只读使用）：
`/etc/reasonix/config.toml`（default_model=deepseek-flash、providers、MCP `[[plugins]]`：mobile-security / jadx-headless）、`/root/hos-ares/reasonix.toml`（项目配置，含 MCP 注册）、`/root/.npm-global`（reasonix 全局）、`/usr/lib/python3.X/site-packages`（mobile-security-mcp 及依赖）。

## 11.3 体积预算与 Tier 分级（全量装载的务实路径）

| Tier | 内容 | rootfs 磁盘 | APK 资产(tar.xz) | 适用 |
|------|------|------------|-----------------|------|
| **A（默认首版）** | Alpine + Node22 + reasonix + mobile-security-mcp(Python：androguard/apkid/apkleaks/frida-tools/mitmproxy/semgrep) | ~420MB | **~150-180MB** | APK 单文件分发（自托管/企业） |
| B（可选下载） | + JRE17 + jadx + apktool + radare2 | +300MB | +100MB | 静态分析深度模式，按需 OTA |
| C（全量） | + frida-server(arm64) + objection + hluda + scrcpy | +150MB | +60MB | 动态插桩/设备控制全开 |

**默认策略**：APK 内嵌 **Tier-A 全量**（保证"装完即用"：ASTA 静态+基础动态+SCA 可用）；Tier-B/C 用 **Play Asset Delivery / 自建增量包** 首用按需下载（Android 12+ 支持 assets on demand）。若走 Play 分发：单 APK>150MB 需用 **App Bundle + Play Asset Delivery**；自托管渠道无限制（约 150-180MB 单 APK）。

## 11.4 CI 流水线（可复现打包）

`.github/workflows/build-apk.yml`：

| Job | 步骤 |
|-----|------|
| bake-rootfs | ubuntu runner：docker run alpine:3.20 → 装 nodejs22/reasonix/MCP 链 → 归档 `/` → 上传 `rootfs.tar.xz` artifact |
| build-apk | Gradle `assembleRelease`（assets 注入 rootfs.tar.xz + proot 二进制）→ 产物 APK |
| smoke-e2e | adb 安装至模拟器(x86_64/arm64) → 首启解压 → serve 健康检查 → `/submit`+SSE 断言 `SSE-OK`（等价 10 文实测）→ **追加 MCP 调用级断言**（12 文：reasonix 会话内发起 `check_tools`，断言 `tool_dispatch`/`tool_result` 帧存在、Agent 正常收尾）→ 报告 APK 体积 |

**验收标准（Phase 5 绿线）**：CI 三 Job 全绿 + smoke-e2e 在模拟器上跑通「安装→解压→serve 就绪→SSE 任务闭环」，APK 体积落在 §11.3 预算内。

## 11.5 偏差记录与风险

| 项 | 原方案 | 实际决策 | 风险/缓解 |
|----|--------|---------|----------|
| 容器来源 | "打包 Termux+proot-distro" | **自备 Alpine rootfs + proot 静态二进制**（不需要用户装 Termux） | rootfs 烘烤需 CI 容器；已脚本化 |
| 分发渠道 | 未明示 | **自托管 APK 优先**；Play 走 AAB+PAD | Play 对"含 bash/Agent 工具"审核严，保留侧载说明 |
| 首启时间 | 未量化 | 解压 150-180MB ≈ 20-40s（闪页+进度条） | 增量解压+Tier 分离缓解 |
| 存储占用 | 未量化 | rootfs 解压 ~420MB + 系统缓存 | 安装引导页明示；可选清理 |
| 工具权限 | MCP 全开 | serve token + 应用内 `deny>ask>allow`（config.toml 固化） | 安全基线保持在 permission 门控内 |

*上一份：`10-Phase4-端到端链路接通.md`*