"""B440 · wheel 必须含全部子包（B439 丢过一次，且当时所有闸门都是绿的）。

判据：`pip wheel` 出来的包里，`hos_ares` 的 bench/doctor/report 与另外四个包都得在。
为什么单独一条：`parity` 的 G2 判"包能不能组出来"，`verify` 判 lint 与账本——
**没有一个在看包里到底装了什么**，于是 137 个文件、G2 绿、`report/` 一个没有。
"""

from __future__ import annotations

import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

#: 必须出现在 wheel 里的包前缀（五个 hos_ares* 包 + 三个产品目录）
REQUIRED = (
    "hos_ares/bench/",
    "hos_ares/doctor/",
    "hos_ares/report/",
    "hos_ares_cli/",
    "hos_ares_core/",
    "hos_ares_oracles/",
    "hos_ares_contract/",
)


def test_wheel_contains_every_subpackage(tmp_path: Path) -> None:
    out = tmp_path / "wheels"
    proc = subprocess.run(  # noqa: S603 - 参数固定，无用户输入
        [sys.executable, "-m", "pip", "wheel", ".", "--no-deps", "-w", str(out)],
        cwd=str(ROOT), capture_output=True, text=True, timeout=420,
    )
    assert proc.returncode == 0, proc.stderr[-1500:]

    wheels = list(out.glob("*.whl"))
    assert wheels, "没产出 wheel"
    names = zipfile.ZipFile(wheels[0]).namelist()
    missing = [pkg for pkg in REQUIRED if not any(n.startswith(pkg) for n in names)]
    assert not missing, f"wheel 缺这些包：{missing}（共 {len(names)} 个文件）"
