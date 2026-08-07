# ============================================================
# HOS-ARES build script (Windows PowerShell) - real build
# Requires: JDK 17, Android SDK (build/android-sdk), Gradle 8.7
# Output: app/app/build/outputs/apk/debug/app-debug.apk
# ============================================================

$ErrorActionPreference = "Stop"

$ROOT   = Split-Path -Parent $MyInvocation.MyCommand.Path
$APP    = Join-Path $ROOT "app"
$SDK    = Join-Path $ROOT "build\android-sdk"
$JDK17  = "C:\Users\46119\AppData\Local\Temp\jdk17\jdk-17.0.20+8"   # local JDK17
$GRADLE = "C:\Users\46119\AppData\Local\Temp\gradle87\gradle-8.7\bin\gradle.bat"

Write-Host "==============================================" -ForegroundColor Cyan
Write-Host "  HOS-ARES build" -ForegroundColor Cyan
Write-Host "==============================================" -ForegroundColor Cyan

# 1. env check
Write-Host "[1/4] checking build environment..."
if (-not (Test-Path $JDK17)) { throw "JDK17 not found: $JDK17" }
if (-not (Test-Path (Join-Path $SDK "platforms\android-34"))) {
    throw "Android SDK platform-34 not found: $SDK"
}
if (-not (Test-Path $GRADLE)) { throw "Gradle not found: $GRADLE" }
Write-Host "      JDK17 / SDK / Gradle OK."

# 2. env vars
$env:JAVA_HOME = $JDK17
$env:PATH = "$JDK17\bin;$env:PATH"

# 3. build
Write-Host "[2/4] building APK (assembleDebug)..."
Push-Location $APP
try {
    & $GRADLE assembleDebug --no-daemon
    if ($LASTEXITCODE -ne 0) { throw "Gradle build failed, exit $LASTEXITCODE" }
} finally {
    Pop-Location
}

# 4. collect artifact
Write-Host "[3/4] collecting artifact..."
$APK = Join-Path $APP "app\build\outputs\apk\debug\app-debug.apk"
$OUT = Join-Path $ROOT "build"
New-Item -ItemType Directory -Force -Path $OUT | Out-Null
$DEST = Join-Path $OUT "HOS-ARES-debug.apk"
Copy-Item $APK $DEST -Force
$size = [math]::Round((Get-Item $DEST).Length / 1KB, 1)

Write-Host "[4/4] done."
Write-Host "    APK: $DEST ($size KB)"
Write-Host "==============================================" -ForegroundColor Cyan
