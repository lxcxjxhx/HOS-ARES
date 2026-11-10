#!/usr/bin/env python3
"""lint-lock-sync —— `pyproject.toml` 的运行期依赖与 `requirements.lock` 必须对得上（B201）。

为什么需要这条：`requirements.lock` 是**内网离线装**的唯一来源（A3「内网能装」）。
它和 `pyproject` 是同一份事实的两个出口，而两个出口最容易走的岔路是：

* 新加一个运行期依赖，只改了 `pyproject` → 离线包装到客户内网，**少一个包**，
  装完才发现 `ModuleNotFoundError`（B200 的 jsonschema 就是这个剧本）；
* 锁里钉的版本**低于**`pyproject` 声明的下限 → 本地能跑，客户装出来是旧版。

三查：

1. `pyproject` 的每个运行期依赖，锁里必须有 pin（缺 → FAIL）；
2. 锁里 pin 的版本必须满足 `pyproject` 的下限（不满足 → FAIL）；
3. 锁里有、`pyproject` 没有的条目：**允许**（那是传递依赖），但必须能说清来源——
   每次输出都列出来，方便人工确认没有孤儿。

用法：``python scripts/lint/lock_sync.py [--json]``
"""

from __future__ import annotations

import json
import re
import sys
import tomllib
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
PYPROJECT = ROOT / "pyproject.toml"
LOCK = ROOT / "requirements.lock"

#: `name>=1.2` / `name==1.2` / `name[extra]>=1.2` —— 只认名字与下限
_REQ = re.compile(r"^(?P<name>[A-Za-z0-9_.\-]+)\s*(?:\[[^\]]*\])?\s*(?P<spec>.*)$")
_FLOOR = re.compile(r">=\s*(?P<v>[0-9][0-9A-Za-z.\-]*)")
_PIN = re.compile(r"^(?P<name>[A-Za-z0-9_.\-]+)\s*==\s*(?P<ver>[^;\s]+)")


def _key(name: str) -> str:
    """包名归一：PEP 503（大小写与 `-_.` 不敏感）。"""
    return re.sub(r"[-_.]+", "-", name).lower()


def _vtuple(ver: str) -> tuple[int, ...]:
    parts = re.findall(r"\d+", ver.split("+")[0])
    return tuple(int(p) for p in parts[:4]) or (0,)


def declared(path: Path = PYPROJECT) -> dict[str, str]:
    """pyproject 的运行期依赖：归一化名字 → 原始声明（含下限）。"""
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    out: dict[str, str] = {}
    for raw in data.get("project", {}).get("dependencies", []) or []:
        m = _REQ.match(str(raw).strip())
        if m:
            out[_key(m.group("name"))] = str(raw).strip()
    return out


def locked(path: Path = LOCK) -> dict[str, str]:
    """锁文件里的 pin：归一化名字 → 版本。注释与空行忽略。"""
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = _PIN.match(line)
        if m:
            out[_key(m.group("name"))] = m.group("ver")
    return out


def compare(deps: dict[str, str], pins: dict[str, str]) -> dict[str, Any]:
    missing = sorted(n for n in deps if n not in pins)
    below: list[str] = []
    for name in sorted(set(deps) & set(pins)):
        floor = _FLOOR.search(deps[name])
        if floor and _vtuple(pins[name]) < _vtuple(floor.group("v")):
            below.append(f"{name}: 锁 {pins[name]} < 下限 {floor.group('v')}")
    extra = sorted(n for n in pins if n not in deps)
    return {"missing": missing, "below_floor": below, "transitive": extra}


#: 容器镜像同样"装什么"要与锁一致：手写清单漏过 jsonschema（B203）
DOCKERFILE = ROOT / "Dockerfile"


def dockerfile_problems(path: Path = DOCKERFILE) -> list[str]:
    """Dockerfile 里装依赖必须走 `-r requirements.lock`，不许手写包名清单。"""
    if not path.exists():
        return []
    problems: list[str] = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith("#") or "pip install" not in stripped:
            continue
        if "-r requirements.lock" in stripped or "--requirement" in stripped:
            continue
        problems.append(
            f"Dockerfile:{lineno}: pip install 没走 requirements.lock（手写清单会漏包）："
            f"{stripped[:90]}"
        )
    return problems


def main(argv: list[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    deps, pins = declared(), locked()
    if not pins:
        print("lint-lock-sync: FAIL 锁文件不存在或没有任何 pin"
              f"（{LOCK.relative_to(ROOT).as_posix()}）", file=sys.stderr)
        return 1
    rows = compare(deps, pins)
    docker_problems = dockerfile_problems()
    if "--json" in args:
        print(json.dumps({"declared": len(deps), "locked": len(pins),
                          "dockerfile": docker_problems, **rows},
                         ensure_ascii=False, indent=2))
        return 0 if not rows["missing"] and not rows["below_floor"] else 1

    bad = False
    if rows["missing"]:
        bad = True
        print("lint-lock-sync: FAIL 这些运行期依赖没进锁（离线包会少包）", file=sys.stderr)
        for name in rows["missing"]:
            print(f"  - {name}（pyproject: {deps[name]}）", file=sys.stderr)
    if rows["below_floor"]:
        bad = True
        print("lint-lock-sync: FAIL 锁里的版本低于 pyproject 下限", file=sys.stderr)
        for item in rows["below_floor"]:
            print(f"  - {item}", file=sys.stderr)
    if docker_problems:
        bad = True
        print("lint-lock-sync: FAIL Dockerfile 装依赖没走锁（B203）", file=sys.stderr)
        for item in docker_problems:
            print(f"  - {item}", file=sys.stderr)
    if bad:
        return 1
    tail = f"；传递依赖 {len(rows['transitive'])} 个（允许）" if rows["transitive"] else ""
    print(f"lint-lock-sync: OK 运行期依赖 {len(deps)} 个全部在锁里、版本不低{tail}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
