"""B225：安装包的“装完点开就是界面”是一条契约，得有用例钉住。

为什么是静态断言而不是真装一遍：本机 silent 安装要提权（UAC），CI 里能跑的那次
（`build-exe` 的"自检安装包"步骤）已经把 装 → 开 → 断言 200 → 卸 → 断言无残留 跑通了。
这里守的是**那份脚本还在说这句话**：快捷方式、收尾页启动、静默时不弹窗。
"""

from __future__ import annotations

import re
from pathlib import Path

ISS = (Path(__file__).resolve().parents[2] / "packaging" / "HOS-ARES.iss").read_text(
    encoding="utf-8")


def _section(name: str) -> str:
    match = re.search(rf"^\[{name}\](.*?)(?=^\[|\Z)", ISS, re.S | re.M)
    assert match, f"安装脚本里没有 [{name}] 段"
    return match.group(1)


def test_start_menu_entry_launches_the_app() -> None:
    icons = _section("Icons")
    assert "{group}\\{#MyAppName}" in icons, "没有开始菜单主入口"
    assert "{app}\\{#MyAppExeName}" in icons, "开始菜单入口没指向 exe"


def test_desktop_icon_is_opt_in() -> None:
    """桌面图标要跟任务开关走：企业环境常常不需要桌面图标。"""
    icons = _section("Icons")
    line = next((ln for ln in icons.splitlines() if "{autodesktop}" in ln), "")
    assert line, "没有桌面快捷方式定义"
    assert "Tasks: desktopicon" in line, "桌面图标没跟任务开关走"


def test_postinstall_opens_the_ui_but_not_when_silent() -> None:
    run = _section("Run")
    assert "{app}\\{#MyAppExeName}" in run
    assert "postinstall" in run, "收尾页没有“立即打开”"
    assert "skipifsilent" in run, "静默安装也会弹窗——企业批量部署会被卡住"


def test_uninstall_keeps_user_outputs_out_of_scope() -> None:
    # 只看**指令行**：注释里提到 out/ 是解释，不是删除范围
    directives = [ln for ln in _section("UninstallDelete").splitlines()
                  if ln.strip() and not ln.strip().startswith(";")]
    assert directives, "卸载段没有任何指令"
    assert all('Name: "{app}"' in ln for ln in directives), directives
    assert not any("out" in ln.lower() for ln in directives), "卸载不该动用户产物目录"
