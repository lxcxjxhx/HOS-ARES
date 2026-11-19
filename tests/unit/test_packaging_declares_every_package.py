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

B444：声明方式从显式列表改成了 `[tool.setuptools.packages.find] include = [...]`
（B439：显式列表丢了 `hos_ares` 的子包）。本用例跟着改成认两种形态——
**模式也算声明**，但每个模式必须真的匹配到包，每个真实包也必须被某个模式匹配。
"""

from __future__ import annotations

import fnmatch
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _patterns() -> list[str]:
    """声明形态：显式包名，或 find.include 里的 glob 模式。"""
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    st = data["tool"]["setuptools"]
    if "packages" in st and isinstance(st["packages"], list):
        return list(st["packages"])
    find = st.get("packages", {}).get("find", {})
    patterns = list(find.get("include") or [])
    assert patterns, "既没有 packages 列表，也没有 packages.find.include——包声明缺失"
    return patterns


def _real_packages() -> set[str]:
    """仓库里真正带 __init__.py 的顶层包（排除工具与产物目录）。"""
    skip = {".venv", "tests", "out", "vendor", "node_modules", "scripts", "plan-hos-ares",
            "profiles", "packaging", "docs", "playbooks"}
    return {
        p.name for p in ROOT.iterdir()
        if p.is_dir() and (p / "__init__.py").exists() and p.name not in skip
    }


def _is_declared(name: str) -> bool:
    return any(fnmatch.fnmatch(name, pat) for pat in _patterns())


def test_every_real_package_is_declared() -> None:
    missing = sorted(p for p in _real_packages() if not _is_declared(p))
    assert not missing, f"这些包不会进 wheel/安装：{missing}（声明：{_patterns()}）"


def test_the_cli_package_is_declared() -> None:
    """少了 hos_ares_cli，`hos_ares` 这个 console script 就 ModuleNotFoundError。"""
    assert _is_declared("hos_ares_cli")


def test_no_phantom_patterns_are_declared() -> None:
    """每个模式都必须真匹配到东西——否则是「声明了但没装」的静默漏装。"""
    real = _real_packages()
    phantom = [pat for pat in _patterns() if not any(fnmatch.fnmatch(p, pat) for p in real)]
    assert not phantom, f"声明了不存在的包：{phantom}"
