# HOS-ARES 完整 rootfs 一键构建 (Windows)
# 需要: Docker Desktop for Windows
#
# 步骤:
#   1. 确保 Docker Desktop 正在运行
#   2. 在项目根目录执行: powershell -ExecutionPolicy Bypass -File scripts\build_rootfs.ps1
#   3. 构建完成后 rootfs.tar 会出现在 app/ares-rootfs/ 目录
#   4. 正常构建 APK 即可 (Android Studio / gradlew assembleDebug)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$AresRootfs = Join-Path $ProjectRoot "app\ares-rootfs"
$DockerFile = Join-Path $PSScriptRoot "Dockerfile.rootfs"
$ImageName = "hosares-rootfs"

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "HOS-ARES 完整 rootfs 构建" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# 检查 Docker
try {
    docker version 2>&1 | Out-Null
    Write-Host "[1] Docker 已就绪" -ForegroundColor Green
} catch {
    Write-Host "[错误] Docker 未运行，请启动 Docker Desktop" -ForegroundColor Red
    exit 1
}

# 构建 Docker 镜像
Write-Host "[2] 构建 Docker 镜像..." -ForegroundColor Yellow
docker build -f $DockerFile -t $ImageName $ProjectRoot
if ($LASTEXITCODE -ne 0) {
    Write-Host "[错误] Docker 镜像构建失败" -ForegroundColor Red
    exit 1
}
Write-Host "[2] Docker 镜像构建完成" -ForegroundColor Green

# 运行容器并导出 rootfs.tar
Write-Host "[3] 导出 rootfs.tar..." -ForegroundColor Yellow
$OutputDir = Join-Path $ProjectRoot "app\ares-rootfs"
if (-not (Test-Path $OutputDir)) {
    New-Item -ItemType Directory -Path $OutputDir -Force | Out-Null
}

docker run --rm -v "${OutputDir}:/output" $ImageName
if ($LASTEXITCODE -ne 0) {
    Write-Host "[错误] rootfs.tar 导出失败" -ForegroundColor Red
    exit 1
}

# 验证输出
$RootfsTar = Join-Path $OutputDir "rootfs.tar"
if (Test-Path $RootfsTar) {
    $SizeMB = [math]::Round((Get-Item $RootfsTar).Length / 1MB, 1)
    Write-Host "[4] rootfs.tar 已生成: $SizeMB MB" -ForegroundColor Green
} else {
    Write-Host "[错误] rootfs.tar 未找到" -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "构建完成!" -ForegroundColor Green
Write-Host "rootfs.tar 位置: $RootfsTar" -ForegroundColor White
Write-Host ""
Write-Host "下一步: 正常构建 APK (Android Studio / gradlew assembleDebug)" -ForegroundColor Yellow
Write-Host "========================================" -ForegroundColor Cyan