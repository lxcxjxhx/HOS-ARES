# Alpine Linux rootfs 构建 / 获取说明（占位）

本文档说明如何为 reasonix-proot-app 提供 Alpine Linux rootfs（根文件系统）。
当前为占位说明，给出两种主流方式（离线获取 / Docker 构建），真实实现时选用其一并在 CI/脚本中固化。

## 一、方案 A：直接获取官方 minirootfs（推荐，最小化）

Alpine 官方提供 `alpine-minirootfs` 压缩包，适合作为 proot 容器的 rootfs。

- 官方下载地址（替换版本号与架构为实际值）：
  `https://dl-cdn.alpinelinux.org/alpine/v3.2x/releases/<arch>/alpine-minirootfs-<version>-<arch>.tar.gz`

- 说明：
  - 架构需与目标设备一致，Android 常见为 `aarch64`（arm64）或 `armv7`，开发机（x86_64）用 `x86_64`。
  - minirootfs 体积小，仅含基础文件系统，不含包管理器索引，后续由 `bootstrap.sh` 执行 `apk update` 补齐。
  - 下载后解压到指定 rootfs 目录即可，例如 `<ROOTFS>/`。

```bash
# 示例（占位，版本/架构按实际调整）
ARCH="aarch64"
ALPINE_VERSION="3.20.0"
ROOTFS="/path/to/rootfs"

mkdir -p "$ROOTFS"
curl -LO "https://dl-cdn.alpinelinux.org/alpine/v3.20/releases/${ARCH}/alpine-minirootfs-${ALPINE_VERSION}-${ARCH}.tar.gz"
tar -xzf "alpine-minirootfs-${ALPINE_VERSION}-${ARCH}.tar.gz" -C "$ROOTFS"
```

## 二、方案 B：使用 Docker 构建（便于定制）

若需要预装大量包、生成可复制的定制 rootfs，可用 Dockerfile 构建后导出文件系统。

- 本目录提供 `Dockerfile` 占位（见下文），使用 `alpine` 官方镜像作为基础。
- 构建并导出 rootfs：

```bash
docker build -t hos-alpine-rootfs ./runtime/alpine
# 导出容器文件系统为 tar，随后解压到 rootfs 目录
id=$(docker create hos-alpine-rootfs)
docker export "$id" | tar -x -C "$ROOTFS"
docker rm "$id"
```

### Dockerfile（占位）

```dockerfile
# 基础镜像使用官方 Alpine（版本按需调整）
FROM alpine:3.20

# 安装基础包（对齐 bootstrap.sh 中的依赖）
RUN apk add --no-cache \
        bash \
        curl \
        wget \
        python3 \
        py3-pip \
        nodejs \
        git \
        openssh-client \
        ca-certificates \
    && adduser -D -s /bin/sh agent

# 设置工作目录与权限
RUN mkdir -p /home/agent /work \
    && chown -R agent:agent /home/agent /work

WORKDIR /work

# 说明：容器仅用于导出 rootfs，无需 ENTRYPOINT/CMD
```

## 三、Android 端接入要点

- reasonix-proot-app 会将选定的 rootfs 目录作为 proot 的 `-r` 参数挂起。
- rootfs 应放在应用私有目录（如 `getFilesDir()` 下），便于应用读写且无需外部存储权限。
- rootfs 就绪后调用 `RuntimeHost.start()` 启动容器，并在容器内执行 `bootstrap.sh` 完成初始化。

## 四、校验

- 解压后确认 `$ROOTFS/etc/alpine-release` 存在，即为有效的 Alpine rootfs。
- 通过 `RuntimeHost.exec("/bin/sh -c 'cat /etc/alpine-release'")` 验证容器启动成功。
