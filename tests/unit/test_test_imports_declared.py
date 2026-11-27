"""B584 · 测试里的第三方 `import` 必须在 `pyproject.toml` 里声明。

## 为什么需要这条

B582 为了让 `_post()` 的返回类型精确，加了 `import httpx` 并把返回标成 `httpx.Response`。
本机跑测试全绿——因为 `httpx` 作为 fastapi 的**传递依赖**被装上了。

CI 上没有它：

```
tests/unit/test_ui_approvals.py:13: in <module>
    import httpx
E   ModuleNotFoundError: No module named 'httpx'
```

**在收集阶段就崩，一个测试都跑不了。**

## 这条判据验什么

扫 `tests/` 下所有 `test_*.py` 的顶层 `import` / `from X import`，把
**标准库**、**本项目模块**、**已声明依赖**之外的挑出来。

已声明依赖从 `pyproject.toml` 读（含 `[project.optional-dependencies]`）。
项目自己的脚本模块（`noroute` / `run_bench` / `verify` 之类，靠 `sys.path` 注入）
用白名单排除。

## 边界

它只管**顶层 import**。函数内的局部 import（本项目常见写法，用来延迟加载）
不在检查范围内——那些即使漏了，也只影响单条用例，不会让整轮收集崩掉。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

#: 项目自己的模块（通过 sys.path 注入，不是 pip 包）
OWN_MODULES = frozenset(
    "hos_ares hos_ares_core hos_ares_cli hos_ares_oracles hos_ares_contract web worker "
    "lab capsule_fixture conftest worker_race script_module".split()
)

#: `scripts/` 下被测试直接 import 的模块（`sys.path` 注入）
SCRIPT_MODULES = frozenset(
    "dead_exports ledger_check ledger_open lock_sync measure_recall measure_discovery "
    "negative_samples netimports nodata_stub noroute policy_check run_bench spec_drift "
    "ui_zero_address verify act_cmd console type_debt type_debt_tests".split()
)

#: import 名与包名不一致的（PyYAML 提供 `yaml` 等）
PACKAGE_ALIASES = {"yaml": "pyyaml"}


def _declared_dependencies() -> set[str]:
    """从 `pyproject.toml` 读所有声明的依赖，规范化成小写连字符形式。

    两种写法都要认（B584 实测）：
      1. 数组内联：`dev = ["pytest>=8.0", "cryptography>=42", ...]`
      2. 数组多行：每行以引号开头
    早先只认第 2 种，于是 `pytest` 被判成"未声明"——**过严**。
    """
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    deps: set[str] = set()
    # 先把整个文件里所有看起来像依赖声明的引号串都捞出来
    for m in re.finditer(r'"([A-Za-z0-9_.\-]+)(?:\[[^\]]*\])?(?:[<>=!~][^"]*)?"', text):
        name = m.group(1)
        # 排除明显的非包名（路径、描述、版本号等）
        if chr(0x2F) in name or name.endswith((".py", ".md", ".json", ".yaml")):
            continue
        if name.lower() in ("readme", "hos-ares", "hos_ares"):
            continue
        deps.add(name.lower().replace("_", "-"))
    return deps


def _top_level_imports(path: Path) -> list[tuple[int, str]]:
    """只取**顶层**（无缩进）的 import。"""
    out: list[tuple[int, str]] = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        if line != line.lstrip():
            continue                      # 缩进 = 局部 import，不查
        m = re.match(r"(?:from|import)\s+([A-Za-z_][A-Za-z0-9_]*)", line)
        if m:
            out.append((lineno, m.group(1)))
    return out


def find_undeclared() -> list[str]:
    """返回所有「测试顶层 import 了、但没在 pyproject 里声明」的模块。"""
    deps = _declared_dependencies()
    stdlib = set(sys.stdlib_module_names)
    problems: list[str] = []
    for path in sorted((ROOT / "tests").rglob("test_*.py")):
        for lineno, mod in _top_level_imports(path):
            if mod in stdlib or mod in OWN_MODULES or mod in SCRIPT_MODULES:
                continue
            if mod.startswith("_") or mod.startswith("test_"):
                continue
            normalized = PACKAGE_ALIASES.get(mod, mod).lower().replace("_", "-")
            if normalized in deps or any(d.startswith(normalized) for d in deps):
                continue
            rel = path.relative_to(ROOT).as_posix()
            problems.append(f"{rel}:{lineno}: {mod}")
    return problems


def test_no_undeclared_third_party_imports_in_tests() -> None:
    """测试文件顶层 import 的第三方包，必须都在 `pyproject.toml` 里声明。

    B582 → B584 的教训：`import httpx` 在本机绿、在 CI 收集阶段就崩。
    """
    problems = find_undeclared()
    assert not problems, (
        "这些顶层 import 指向未声明的依赖（CI 上会 ModuleNotFoundError）：\n  "
        + "\n  ".join(problems[:10])
    )


def test_the_checker_itself_can_report_a_problem() -> None:
    """**量具能红**：伪造一条未声明 import，检查必须报出来。

    没有这一条，上面那条可能是恒绿的（`_declared_dependencies()` 解析失败时
    会返回空集，然后所有 import 都被判为"未声明"——那反而是过严；反过来若
    正则写错匹配不到，所有 import 都被放行则是过松）。这里两头都试一下。
    """
    assert "httpx" not in {d.lower() for d in _declared_dependencies()}, (
        "`httpx` 居然被声明了——那 B584 的结论要重看"
    )
    # 正常状态：没有未声明的
    assert not find_undeclared()


def test_declared_dependencies_are_actually_read() -> None:
    """`pyproject.toml` 至少要解析出若干条依赖，否则上面的判据没有意义。"""
    deps = _declared_dependencies()
    assert len(deps) >= 5, f"只解析到 {len(deps)} 条依赖：{sorted(deps)}"
    assert "pyyaml" in deps, "PyYAML 应该在声明里"
