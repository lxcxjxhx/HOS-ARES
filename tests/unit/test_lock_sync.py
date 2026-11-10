"""B201：`requirements.lock` 与 `pyproject` 是同一份事实的两个出口。

`requirements.lock` 是**内网离线装**的唯一来源（A3「内网能装」）。它和 `pyproject`
最容易走的岔路有两条，都只在客户内网才显形：

* 新加运行期依赖只改了 `pyproject` → 离线包少一个包（B200 的 jsonschema 就是这个剧本）；
* 锁里钉的版本**低于**`pyproject` 下限 → 本地能跑，客户装出来是旧版。

这组用例钉四件事（全部用临时文件，不碰真仓）：
1. 缺 pin → 红，并点名缺的是哪个；
2. 版本低于下限 → 红，并写清"锁 X < 下限 Y"；
3. 锁里有、pyproject 没有 → 允许（传递依赖），但要列出来；
4. 包名归一（`PyYAML` 与 `pyyaml`、`-` 与 `_` 视为同一个）。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for extra in (ROOT, ROOT / "scripts", ROOT / "scripts" / "lint"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

PYPROJECT = """
[project]
name = "demo"
dependencies = [
    "PyYAML>=6.0",
    "jsonschema>=4.20",
]
"""


def _write(tmp_path: Path, lock_body: str) -> tuple[Path, Path]:
    py = tmp_path / "pyproject.toml"
    py.write_text(PYPROJECT, encoding="utf-8")
    lock = tmp_path / "requirements.lock"
    lock.write_text(lock_body, encoding="utf-8")
    return py, lock


def test_missing_pin_is_reported(tmp_path: Path) -> None:
    import lock_sync

    py, lock = _write(tmp_path, "PyYAML==6.0.2\n")
    rows = lock_sync.compare(lock_sync.declared(py), lock_sync.locked(lock))
    assert rows["missing"] == ["jsonschema"], rows


def test_version_below_floor_is_reported(tmp_path: Path) -> None:
    import lock_sync

    py, lock = _write(tmp_path, "PyYAML==6.0.2\njsonschema==4.10.0\n")
    rows = lock_sync.compare(lock_sync.declared(py), lock_sync.locked(lock))
    assert rows["missing"] == []
    assert rows["below_floor"], "低于下限没被抓住"
    assert "4.10.0" in rows["below_floor"][0] and "4.20" in rows["below_floor"][0]


def test_transitive_pins_are_allowed_but_listed(tmp_path: Path) -> None:
    import lock_sync

    py, lock = _write(tmp_path, "PyYAML==6.0.2\njsonschema==4.26.0\nattrs==26.1.0\n")
    rows = lock_sync.compare(lock_sync.declared(py), lock_sync.locked(lock))
    assert rows["missing"] == [] and rows["below_floor"] == []
    assert rows["transitive"] == ["attrs"]


def test_names_are_normalised(tmp_path: Path) -> None:
    import lock_sync

    py, lock = _write(tmp_path, "pyyaml==6.0.2\njsonschema==4.26.0\n")
    rows = lock_sync.compare(lock_sync.declared(py), lock_sync.locked(lock))
    assert rows["missing"] == [], "PyYAML 与 pyyaml 应当视为同一个包"


def test_real_repo_lock_matches_pyproject() -> None:
    """真仓这一对必须始终对得上——它就是离线包的输入。"""
    import lock_sync

    rows = lock_sync.compare(lock_sync.declared(), lock_sync.locked())
    assert rows["missing"] == [], f"运行期依赖没进锁：{rows['missing']}"
    assert rows["below_floor"] == [], rows["below_floor"]


# ── B203：容器镜像的依赖也必须走锁 ─────────────────────────────
def test_dockerfile_with_hardcoded_list_is_flagged(tmp_path: Path) -> None:
    """手写清单在这里漏过 jsonschema：镜像里加载剧本即崩。"""
    import lock_sync

    df = tmp_path / "Dockerfile"
    df.write_text(
        "FROM python:3.12-slim\n"
        "RUN pip install --no-cache-dir pydantic PyYAML fastapi uvicorn\n",
        encoding="utf-8",
    )
    problems = lock_sync.dockerfile_problems(df)
    assert problems and "没走 requirements.lock" in problems[0], problems


def test_dockerfile_reading_the_lock_is_accepted(tmp_path: Path) -> None:
    import lock_sync

    df = tmp_path / "Dockerfile"
    df.write_text(
        "FROM python:3.12-slim\n"
        "COPY requirements.lock ./\n"
        "RUN pip install --no-cache-dir -r requirements.lock\n",
        encoding="utf-8",
    )
    assert lock_sync.dockerfile_problems(df) == []


def test_real_dockerfile_reads_the_lock() -> None:
    """真仓的 Dockerfile 必须走锁——它就是镜像里那唯一一份依赖清单。"""
    import lock_sync

    assert lock_sync.dockerfile_problems() == []
