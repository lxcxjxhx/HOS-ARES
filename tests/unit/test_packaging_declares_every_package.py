"""B283 · 产品得装得上：`pip install` 出来的包里必须有 CLI。

改前实测（照抄报告里的复现命令，在报告所在目录执行）：

```
$ hos-ares replay --capsule capsules/xxx.json --mode offline
ModuleNotFoundError: No module named 'hos_ares_cli'
```

根因不在 CLI，在打包：`pyproject.toml` 的 `[tool.setuptools] packages` 只声明了
`hos_ares_core` 与 `api` 两个包——`hos_ares_cli` / `hos_ares` / `hos_ares_oracles` /
`hos_ares_contract` / `web` / `worker` 全都不进包，于是 console script 一跑就炸。
"每条发现都能复现"是产品承诺，而这条承诺依赖 CLI 在任何目录可用。
"""

from __future__ import annotations

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _declared() -> list[str]:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return list(data["tool"]["setuptools"]["packages"])


def _real_packages() -> set[str]:
    """仓库里真正带 __init__.py 的顶层包（排除工具与产物目录）。"""
    skip = {".venv", "tests", "out", "vendor", "node_modules", "scripts", "plan-hos-ares",
            "plan-hos-ares", "profiles", "packaging", "docs", "playbooks"}
    return {
        p.name for p in ROOT.iterdir()
        if p.is_dir() and (p / "__init__.py").exists() and p.name not in skip
    }


def test_every_real_package_is_declared() -> None:
    missing = sorted(_real_packages() - set(_declared()))
    assert not missing, f"这些包不会进 wheel/安装：{missing}"


def test_the_cli_package_is_declared() -> None:
    """少了 hos_ares_cli，`hos_ares` 这个 console script 就 ModuleNotFoundError。"""
    assert "hos_ares_cli" in _declared()


def test_no_phantom_packages_are_declared() -> None:
    """声明了但目录不存在，setuptools 会报错或静默漏装——两边都得对得上。"""
    phantom = sorted(set(_declared()) - _real_packages())
    assert not phantom, f"声明了不存在的包：{phantom}"
