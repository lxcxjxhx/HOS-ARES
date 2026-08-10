@echo off
REM ============================================================
REM  HOS-ARES —— 一键构建 APK
REM  前置要求:
REM    - JDK 17+ (Android Studio 自带的 JBR 也可)
REM    - Android SDK (platforms;android-34, build-tools;34.0.0)
REM  自动探测 SDK/JDK；也可用环境变量 ANDROID_HOME / JAVA_HOME 指定。
REM  产物: ..\dist\hos-ares.apk
REM ============================================================
setlocal enabledelayedexpansion

REM ---- 探测 Android SDK ----
if defined ANDROID_HOME (
    set "SDK=%ANDROID_HOME%"
) else if defined ANDROID_SDK_ROOT (
    set "SDK=%ANDROID_SDK_ROOT%"
) else if exist "%LOCALAPPDATA%\Android\Sdk" (
    set "SDK=%LOCALAPPDATA%\Android\Sdk"
) else if exist "%ProgramFiles%\Android\Sdk" (
    set "SDK=%ProgramFiles%\Android\Sdk"
) else (
    echo [FAIL] 未找到 Android SDK。请安装 Android Studio 或设置 ANDROID_HOME 环境变量。
    exit /b 1
)
echo [SDK] %SDK%

REM ---- 探测 JDK 17+ ----
if defined JAVA_HOME (
    set "JAVA=%JAVA_HOME%"
) else if exist "%ProgramFiles%\Android\Android Studio\jbr" (
    set "JAVA=%ProgramFiles%\Android\Android Studio\jbr"
) else if exist "%LOCALAPPDATA%\Programs\Android Studio\jbr" (
    set "JAVA=%LOCALAPPDATA%\Programs\Android Studio\jbr"
) else (
    where java >nul 2>nul
    if not errorlevel 1 (
        echo [JAVA] 使用 PATH 中的 java
        goto :build
    )
    echo [FAIL] 未找到 JDK 17+。请安装 JDK 17 或 Android Studio，或设置 JAVA_HOME。
    exit /b 1
)
if exist "%JAVA%\bin\java.exe" (
    echo [JAVA] %JAVA%
) else (
    echo [FAIL] JAVA_HOME 无效: %JAVA%
    exit /b 1
)

:build
call gradlew.bat assembleDebug
if errorlevel 1 exit /b 1

if exist app\build\outputs\apk\debug\app-debug.apk (
    copy /Y app\build\outputs\apk\debug\app-debug.apk ..\dist\hos-ares.apk >nul
    echo.
    echo [OK] APK 已生成: ..\dist\hos-ares.apk
) else (
    echo [FAIL] 构建失败，请检查上方日志
)
endlocal
