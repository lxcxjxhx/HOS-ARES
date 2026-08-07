# runtime/ — reasonix-proot-app 宿主 Runtime 集成层

本目录负责 HOS-ARES 的底层 Runtime：**在 Android 上提供 Linux 运行环境（Android Linux Runtime）+ Alpine Linux 环境**，作为 AI Agent 的宿主。

我们不重新开发 Agent 框架，而是复用 **reasonix-proot-app** 作为底层 Runtime 提供者。

## 一、职责

- **宿主集成**：封装 reasonix-proot-app 提供的 Android Linux Runtime，负责 proot 容器的启动、停止与生命周期管理。
- **环境提供**：为上层 Agent 提供最小化的 Alpine Linux 运行环境（rootfs + 基础包 + agent 用户）。
- **命令执行**：提供统一的 `exec(command)` 接口，让上层（gateway / agents）无需关心 proot 细节即可在容器内执行命令。
- **端口映射**：为容器内服务（如 Agent Server）预留端口映射能力。

## 二、与 reasonix-proot-app 的集成方式

reasonix-proot-app 是一个解决"在 Android 上运行 Linux Agent"问题的 proot 容器宿主应用。
HOS-ARES 的 APK 层（`app/`）在其内部嵌入/依赖该应用提供的 Android Linux Runtime，集成关系如下：

```
┌──────────────────────────────────────────────┐
│  app/  (Android APK 层)                      │
│  ┌────────────────────────────────────────┐  │
│  │ reasonix-proot-app (Android Linux RT)  │  │
│  │  - 负责把 Alpine rootfs 用 proot 挂起   │  │
│  │  - 提供容器进程 / 文件系统隔离          │  │
│  └────────────────────────────────────────┘  │
│                    │  exec / start / stop    │
│                    ▼                         │
│  runtime/integration/host.py  (RuntimeHost)   │
│  runtime/alpine/       (Alpine rootfs + 引导) │
└──────────────────────────────────────────────┘
        │
        ▼
   gateway/  →  agents/  (Agent 宿主)
```

- **真实运行位置**：`RuntimeHost` 的 `start()` / `exec()` 最终会调用 Android 内置的 `proot` 二进制（由 reasonix-proot-app 提供/打包），以 `proot -r <rootfs> ...` 方式在容器内执行命令。
- **脚手架说明**：当前仓库为纯 Python 脚手架，`host.py` 使用 `subprocess` 做占位实现，`setup.ps1` 用于在开发机（Windows）上准备/验证 Alpine rootfs 与 proot 环境。

## 三、Alpine Linux 环境的启动流程

1. **准备 rootfs**：通过 `runtime/alpine/notes.md` 说明的方式获取/构建 Alpine rootfs（最小化、含基础包与 agent 用户）。
2. **准备 proot**：确保 Android 端（或开发机）存在 `proot` 二进制（reasonix-proot-app 已内置到 APK；开发机可用 `setup.ps1` 安装）。
3. **启动容器**：调用 `RuntimeHost.start()`，以 `proot -r <rootfs>` 方式启动容器，随后在容器内执行 `bootstrap.sh` 完成环境初始化（安装基础包、创建 agent 用户等）。
4. **执行命令**：通过 `RuntimeHost.exec(command)` 在容器内运行任意命令（Agent 启动、安全工具等）。
5. **停止容器**：调用 `RuntimeHost.stop()` 结束容器进程、清理资源。

## 四、目录结构

```
runtime/
├── README.md                # 本文件：职责 / 集成 / 启动流程
├── setup.ps1                # (Windows) 环境准备脚本：准备 proot 与 Alpine rootfs
├── alpine/
│   ├── bootstrap.sh         # Alpine 容器内初始化脚本（占位）
│   └── notes.md             # 如何构建 / 获取 Alpine rootfs（占位说明）
└── integration/
    └── host.py              # RuntimeHost 类：start/stop/exec/is_running（占位实现）
```

## 五、当前进度

- [x] 目录骨架与职责说明
- [x] Alpine 引导脚本占位（`bootstrap.sh`）
- [x] RuntimeHost 接口占位实现（`host.py`）
- [x] 环境准备脚本占位（`setup.ps1`）
- [ ] 对接真实 reasonix-proot-app / Android proot 二进制
- [ ] 端口映射与生命周期守护

> 当前为脚手架阶段，均为占位实现，真实逻辑待接入 Android 端后填充。
