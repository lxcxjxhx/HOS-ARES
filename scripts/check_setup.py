"""B539 · 本地验一遍**安装包**：装得上、装了能打开、卸得干净。

CI 走 `.github/workflows/build-exe.yml` 的第 4 步（silent install → 跑装出来的 exe →
断言页面 → silent uninstall → 断言无残留）。本机没有对应入口，于是
"安装包能不能用"只剩 CI 一句话，本地无从复核。

**为什么默认要 `/DMyPrivileges=lowest`**：交付物装 Program Files 需要管理员
（`H33 §10.2` 建议 A 维持）。但"装了能不能打开"这句话，在**没有管理员**的机器上
也必须能验——否则一整天的工作只换来一个"CI 上大概没问题"。
所以这里编译一个**只用于验证**的安装包（装 `%LocalAppData%`），验的是安装机制本身：
文件落对了、快捷方式建了、exe 起得来、卸载删干净。

产物路径按 CI 的约定（`dist/HOS-ARES.exe`），因为 `.iss` 就是从那儿取 exe 的。
"""

from __future__ import annotations

import json
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist" / "HOS-ARES.exe"
OUT_DIR = ROOT / "packaging" / "out"
ISCC_CANDIDATES = (
    r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
    r"C:\Program Files\Inno Setup 6\ISCC.exe",
)


def _iscc() -> Path | None:
    for cand in ISCC_CANDIDATES:
        path = Path(cand)
        if path.exists():
            return path
    return None


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _build(version: str, *, privileges: str) -> Path:
    """编一个安装包。`privileges=lowest` 时装用户目录，不需要管理员。"""
    iscc = _iscc()
    if iscc is None:
        raise SystemExit("没有 ISCC.exe（装 Inno Setup 6 后再跑）")
    if not DIST.exists():
        raise SystemExit(f"没有 {DIST}：先跑 make exe")
    script = ROOT / "packaging" / "HOS-ARES.iss"
    cmd = [str(iscc), f"/DMyAppVersion={version}", f"/DMyPrivileges={privileges}", str(script)]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0 or "Successful compile" not in proc.stdout:
        raise SystemExit(f"编译失败：\n{proc.stdout[-1200:]}\n{proc.stderr[-400:]}")
    built = OUT_DIR / f"HOS-ARES-Setup-{version}.exe"
    if not built.exists():
        raise SystemExit(f"编译声明成功但没产物：{built}")
    return built


def _install(setup: Path, target: Path) -> int:
    """静默装到指定目录。返回退出码（0 = 成功）。"""
    cmd = [str(setup), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART",
           f"/DIR={target}", "/NOICONS"]
    return subprocess.run(cmd, capture_output=True).returncode


def _uninstall(target: Path) -> int:
    exe = target / "unins000.exe"
    if not exe.exists():
        return 1
    return subprocess.run(
        [str(exe), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"],
        capture_output=True,
    ).returncode


def _kill_by_path(exe: Path) -> int:
    """按**可执行文件路径**杀掉装出来的进程（含 PyInstaller 的子进程）。

    **卸载前必须先停**：Windows 删不掉正在运行的 exe，Inno 会把这个文件留下并
    建议重启。验证脚本要自己收拾干净，否则"残留"是自找的假故障。

    按路径匹配而不是按名字：用户可能同时在别处跑 out/dist 下那个，不能误杀。
    """
    script = (
        "Get-CimInstance Win32_Process | "
        f"Where-Object {{ $_.ExecutablePath -eq '{exe}' }} | "
        "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
    )
    subprocess.run(["powershell", "-NoProfile", "-Command", script], capture_output=True)
    for _ in range(20):
        alive = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             f"(Get-CimInstance Win32_Process | Where-Object {{ $_.ExecutablePath -eq '{exe}' }}).Count"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        if (alive.stdout or "0").strip() in ("0", ""):
            return 0
        time.sleep(0.5)
    return 1


def _page_answers(exe: Path, port: int, timeout_s: float = 90.0) -> tuple[int, str]:
    """起装出来的 exe，取一次页面。返回 (http 状态, **整页**内容)。

    整页而不是前 4 KB：`id="nav"` 在样式表之后（`web/static/index.html` 30 KB），
    只读开头会把"页面正常"判成"没有导航"——那是量具太短，不是产品坏了。
    """
    proc = subprocess.Popen(  # noqa: S603 - 固定可执行 + 本机回环
        [str(exe), "--no-browser", "--wait-s", "90", "--port", str(port)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    deadline = time.time() + timeout_s
    try:
        while time.time() < deadline:
            try:
                url = f"http://127.0.0.1:{port}/ui/app"
                with urllib.request.urlopen(url, timeout=2.0) as resp:
                    return resp.status, resp.read().decode("utf-8", "replace")
            except Exception:  # noqa: BLE001 - 还没起来就继续等
                if proc.poll() is not None:
                    return 0, f"进程提前退出：rc={proc.returncode}"
                time.sleep(0.5)
        return 0, "超时未起服务"
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()


def main(argv: list[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    version = "0.0.0-check"
    for i, a in enumerate(args):
        if a == "--version" and i + 1 < len(args):
            version = args[i + 1]
    privileges = "lowest"
    if "--as-shipped" in args:
        privileges = "admin"        # 交付口径：需要管理员，本机通常跑不了

    target = Path.home() / "AppData" / "Local" / "HOS-ARES-check"
    report: dict[str, object] = {"privileges": privileges, "target": str(target)}
    problems: list[str] = []

    if target.exists():
        shutil.rmtree(target, ignore_errors=True)

    print(f"[1/4] 编译安装包（PrivilegesRequired={privileges}）…")
    setup = _build(version, privileges=privileges)
    report["setup"] = str(setup)
    report["setup_bytes"] = setup.stat().st_size
    print(f"      {setup.name} {setup.stat().st_size / 1e6:.1f} MB")

    print(f"[2/4] 静默安装到 {target} …")
    rc = _install(setup, target)
    report["install_rc"] = rc
    installed_exe = target / "HOS-ARES.exe"
    if rc != 0 or not installed_exe.exists():
        problems.append(f"安装失败：rc={rc}，exe 在不在？{installed_exe.exists()}")
    else:
        quickstart = target / "快速开始.txt"
        report["quickstart"] = quickstart.exists()
        if not quickstart.exists():
            problems.append("装完没有「快速开始.txt」——说明书没随装随带")

        print("[3/4] 跑装出来的 exe，取一次页面 …")
        port = _free_port()
        status, body = _page_answers(installed_exe, port)
        report["http_status"] = status
        ok_page = status == 200 and "<title>" in body and 'id="nav"' in body
        report["page_ok"] = ok_page
        if not ok_page:
            problems.append(f"装出来的 exe 没给出控制台页面：status={status} body={body[:120]!r}")

        print("[4/4] 停进程 → 静默卸载 → 检查残留 …")
        _kill_by_path(installed_exe)
        rc_un = _uninstall(target)
        report["uninstall_rc"] = rc_un
        time.sleep(2)
        leftovers = sorted(p.name for p in target.glob("*")) if target.exists() else []
        report["leftovers"] = leftovers
        if leftovers:
            problems.append(f"卸载后仍有残留：{leftovers}")

    report["problems"] = problems
    print()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if problems:
        print(f"\n结论：{len(problems)} 个问题")
        return 1
    print("\n结论：装得上 · 装了能打开 · 卸得干净")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
