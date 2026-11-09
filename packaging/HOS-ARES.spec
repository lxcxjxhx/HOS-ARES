# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包描述（B198）：一个 exe，双击就起控制台并打开界面。

为什么单文件：交付对象是"拿到就能跑"的人，不是开发机。装 Python / venv / 依赖
不在他们的路径上；一个 exe 是最短的路。

打包口径（与 CI 的 `.github/workflows/build-exe.yml` 一致）：
* 入口是 `veripen_cli/desktop.py`（起服务 → 打印地址 → 打开浏览器）；
* `web/static` 必须整目录带上（页面与 router.js 是数据文件，不是 import）；
* 自己的包用 `collect_submodules` 收全，避免"某个模块只在运行期才被 import"时漏掉。

本地复现：
    python -m PyInstaller packaging/HOS-ARES.spec --noconfirm
产物：dist/HOS-ARES.exe
"""

import os

from PyInstaller.utils.hooks import collect_submodules

#: spec 里的相对路径以 CWD 解析，靠不住；统一从 spec 所在目录推仓库根
ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))

hidden = []
for pkg in ("web", "veripen_core", "veripen_cli", "veripen_oracles", "veripen"):
    hidden += collect_submodules(pkg)

# uvicorn 的循环/协议是运行期按字符串选的，静态分析看不到
hidden += [
    "uvicorn.logging",
    "uvicorn.loops.auto",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan.on",
]

a = Analysis(
    [os.path.join(ROOT, "veripen_cli", "desktop.py")],
    pathex=[ROOT],
    binaries=[],
    datas=[(os.path.join(ROOT, "web", "static"), os.path.join("web", "static"))],
    hiddenimports=sorted(set(hidden)),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["playwright", "pytest", "mypy"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="HOS-ARES",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,          # 保留控制台：用户要看到地址与"数据：…"那两行
    disable_windowed_traceback=False,
)
