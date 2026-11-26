"""B539 · 安装包的行为口径（可离线验的那部分）。

"装了能不能打开"这句话以前只有 CI 一句话。这一批把安装机制本地化了，
但**真装一遍**要 80 秒、要 ISCC、要能写 %LocalAppData%——那不该进每次闸门。
所以这里只钉**不需要真装**的那部分：脚本形状、静默参数、以及三条最容易踩空的约定。

真装的证据在 `python scripts/check_setup.py`（约 80 秒），它的输出是 JSON，
可以直接当证据贴进账本。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ISS = ROOT / "packaging" / "HOS-ARES.iss"
SETUP_SCRIPT = ROOT / "scripts" / "check_setup.py"


def _iss() -> str:
    return ISS.read_text(encoding="utf-8")


# ── 交付口径：默认仍要管理员 ──────────────────────────────────
def test_default_privilege_is_still_admin() -> None:
    """`H33 §10.2` 建议 A：维持 CI 覆盖。默认装 Program Files 才需要 admin。

    这条挡的是"为了本地好验而把交付物改成 lowest"——那是拿交付换便利。
    """
    text = _iss()
    assert 'PrivilegesRequired={#MyPrivileges}' in text
    assert 'ifndef MyPrivileges' in text
    assert '#define MyPrivileges "admin"' in text, "默认必须还是 admin"


def test_privilege_can_be_overridden_for_verification() -> None:
    """能覆盖 ≠ 默认改掉：`/DMyPrivileges=lowest` 只用于验证安装机制。"""
    assert 'ifndef MyPrivileges' in _iss()
    assert "lowest" in SETUP_SCRIPT.read_text(encoding="utf-8")


# ── 装完必须带说明书 ─────────────────────────────────────────
def test_quickstart_is_shipped_with_the_installer() -> None:
    """装完找不到说明书 = 出问题时只能问人。文件缺失用 skipif 容错（打包机可能没有 docs）。"""
    text = _iss()
    assert "快速开始.txt" in text
    assert "skipifsourcedoesntexist" in text, "缺说明书时别让编译整个失败"
    assert (ROOT / "docs" / "exe-quickstart.txt").exists()


# ── 卸载不能动用户产物 ───────────────────────────────────────
def test_uninstall_only_removes_the_app_dir() -> None:
    """`{app}` 之外一律不动：用户的 out/ 产物不在安装目录里，卸载不许碰。"""
    text = _iss()
    uninstall_block = text.split("[UninstallDelete]", 1)[1]
    names = re.findall(r"Name:\s*\"([^\"]+)\"", uninstall_block)
    assert names == ["{app}"], f"卸载删了 {names}——只允许删 {{app}}"


# ── 验证脚本自己的三条纪律 ───────────────────────────────────
def test_check_script_stops_processes_before_uninstall() -> None:
    """Windows 删不掉正在运行的 exe：卸载前不停进程，就等着看"残留"假故障。

    这条是实测踩出来的——第一次跑就报 `leftovers: ["HOS-ARES.exe"]`，
    而那个文件其实是"程序还在跑"，不是卸载没删。
    """
    src = SETUP_SCRIPT.read_text(encoding="utf-8")
    uninstall_at = src.index("_uninstall(target)")
    kill_at = src.rindex("_kill_by_path", 0, uninstall_at)
    assert kill_at < uninstall_at, "卸载前必须先停进程"


def test_check_script_reads_the_whole_page() -> None:
    """`id="nav"` 在 30 KB 页面的样式表之后：只读前 4 KB 会把正常页面判成没导航。

    同一条实测踩出来的（第一次 `page_ok: false`，而页面其实完全正常）。
    """
    src = SETUP_SCRIPT.read_text(encoding="utf-8")
    assert "resp.read()" in src, "要读整页，不许截断"
    assert "resp.read(4000)" not in src


def test_check_script_is_documented_in_the_makefile() -> None:
    make = (ROOT / "Makefile").read_text(encoding="utf-8")
    assert "installer-check:" in make
    assert "scripts/check_setup.py" in make


@pytest.mark.skipif(sys.platform != "win32", reason="安装包只在 Windows 上构建")
def test_iscc_presence_is_reported_not_assumed() -> None:
    """没有 ISCC 时要给出可读的错，而不是 FileNotFoundError。"""
    src = SETUP_SCRIPT.read_text(encoding="utf-8")
    assert "ISCC_CANDIDATES" in src
    assert "没有 ISCC.exe" in src
