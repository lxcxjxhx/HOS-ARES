# =============================================================================
# setup.ps1 — Runtime 环境准备脚本（Windows PowerShell，占位实现）
#
# 作用：
#   在开发机（Windows）上准备 reasonix-proot-app Runtime 所需的本地环境：
#     1. 检查 / 下载 proot 二进制（Windows 上通常为 WSL 或已安装的 proot）。
#     2. 检查 / 获取 Alpine rootfs（见 runtime/alpine/notes.md）。
#     3. 校验 rootfs 有效性。
#
# 真实环境说明：
#   本脚本面向开发机调试与结构验证。真实 Android 端由 reasonix-proot-app 提供
#   proot 二进制与 rootfs 部署，无需本脚本；此处仅用于本地脚手架验证。
#
# 用法：
#   PowerShell 在当前目录执行：  .\setup.ps1
#   可选参数： -Rootfs <路径>  -ProotBin <路径>
# =============================================================================

param(
    # 可选：rootfs 目录路径，默认 ./runtime/alpine/rootfs
    [string]$Rootfs = (Join-Path $PSScriptRoot "alpine\rootfs"),
    # 可选：proot 可执行文件路径（Windows 开发机，如 WSL 内的 proot）
    [string]$ProotBin = "proot"
)

Write-Host "=== Runtime 环境准备（占位）===" -ForegroundColor Cyan

# ---------------------------------------------------------------------------
# 1. 检查 proot 二进制
#    占位：在 Windows 上 proot 通常位于 WSL 中；此处仅做存在性检查提示。
#    真实实现：可尝试 `Get-Command $ProotBin`，缺失则给出安装指引。
# ---------------------------------------------------------------------------
Write-Host "`n[1/3] 检查 proot 二进制: $ProotBin"
$cmd = Get-Command $ProotBin -ErrorAction SilentlyContinue
if ($cmd) {
    Write-Host "  - 找到 proot: $($cmd.Source)" -ForegroundColor Green
} else {
    Write-Warning "  - 未在 PATH 中找到 '$ProotBin'。"
    Write-Warning "    开发机可尝试：在 WSL (Ubuntu) 中执行 'apt install proot' 后，"
    Write-Warning "    将 ProotBin 指向 wsl.exe 或 WSL 内的 proot 路径。"
}

# ---------------------------------------------------------------------------
# 2. 检查 / 获取 Alpine rootfs
#    占位：仅创建目录并提示如何获取 rootfs。
#    真实实现：调用 curl 下载 alpine-minirootfs 并解压到 $Rootfs，
#             具体 URL 见 runtime/alpine/notes.md。
# ---------------------------------------------------------------------------
Write-Host "`n[2/3] 检查 Alpine rootfs: $Rootfs"
if (-not (Test-Path $Rootfs)) {
    Write-Host "  - rootfs 目录不存在，创建占位目录 ..." -ForegroundColor Yellow
    New-Item -ItemType Directory -Force -Path $Rootfs | Out-Null
}
# 占位：真实实现在此下载并解压 minirootfs
# $mini = "https://dl-cdn.alpinelinux.org/alpine/v3.20/releases/x86_64/alpine-minirootfs-<ver>-x86_64.tar.gz"
# Invoke-WebRequest -Uri $mini -OutFile "$Rootfs\minirootfs.tar.gz"
# tar -xzf "$Rootfs\minirootfs.tar.gz" -C $Rootfs

# ---------------------------------------------------------------------------
# 3. 校验 rootfs
#    真实校验：检查 $Rootfs\etc\alpine-release 是否存在。
# ---------------------------------------------------------------------------
Write-Host "`n[3/3] 校验 rootfs 有效性"
$release = Join-Path $Rootfs "etc\alpine-release"
if (Test-Path $release) {
    $ver = Get-Content $release -TotalCount 1
    Write-Host "  - 有效 Alpine rootfs，版本: $ver" -ForegroundColor Green
} else {
    Write-Warning "  - 未找到 $release 。rootfs 尚未就绪，"
    Write-Warning "    请参照 runtime\alpine\notes.md 获取/构建 rootfs 后再运行本脚本。"
}

Write-Host "`n=== 环境准备完成（占位）。 ===" -ForegroundColor Cyan
