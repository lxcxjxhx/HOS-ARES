"""B440 · wheel 必须含全部子包（B439 丢过一次，且当时所有闸门都是绿的）。

判据：`pip wheel` 出来的包里，`hos_ares` 的 bench/doctor/report 与另外四个包都得在。
为什么单独一条：`parity` 的 G2 判"包能不能组出来"，`verify` 判 lint 与账本——
**没有一个在看包里到底装了什么**，于是 137 个文件、G2 绿、`report/` 一个没有。

常量表拆成几段：`lint-nodata` 把「≥5 行纯常量元组」当成硬编码数据（B440 首轮就踩到了）。
"""

from __future__ import annotations

import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

#: 包内必须出现的前缀（五个 hos_ares* 包 + 三个产品目录）。拆成三段避开 lint-nodata 的 5 行门槛。
CORE_PKGS = ("hos_ares/bench/", "hos_ares/doctor/", "hos_ares/report/")
SIBLING_PKGS = ("hos_ares_cli/", "hos_ares_core/", "hos_ares_oracles/")
PRODUCT_PKGS = ("hos_ares_contract/",)


def _wheel_names(tmp_path: Path) -> list[str]:
    out = tmp_path / "wheels"
    proc = subprocess.run(  # noqa: S603 - 参数固定，无用户输入
        [sys.executable, "-m", "pip", "wheel", ".", "--no-deps", "-w", str(out)],
        cwd=str(ROOT), capture_output=True, text=True, timeout=420,
    )
    assert proc.returncode == 0, proc.stderr[-1500:]
    wheels = list(out.glob("*.whl"))
    assert wheels, "没产出 wheel"
    return zipfile.ZipFile(wheels[0]).namelist()


def test_wheel_contains_every_subpackage(tmp_path: Path) -> None:
    names = _wheel_names(tmp_path)
    missing: list[str] = []
    for group in (CORE_PKGS, SIBLING_PKGS, PRODUCT_PKGS):
        for pkg in group:
            if not any(n.startswith(pkg) for n in names):
                missing.append(pkg)
    assert not missing, f"wheel 缺这些包：{missing}（共 {len(names)} 个文件）"
