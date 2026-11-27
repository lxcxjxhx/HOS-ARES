"""`tests/` 的类型检查欠账台账（B577）。

## 为什么单独一个台账

`scripts/` 的欠账（74 → 0，B570–B574）已经并入主闸门。`tests/` 是**下一个更大的坑**：

```
文件 180 · 有错 106 · 错误总数 599
```

`tests/` 不在 `MYPP_TARGETS` 里，所以这 599 个错误从来没被任何闸门看过。

## 为什么不整目录扫

`mypy --strict tests/` 直接失败：

```
tests/conftest.py: error: Duplicate module named "conftest" (also at "tests/unit/conftest.py")
```

pytest 允许每个目录各有一个 `conftest.py`，而 mypy 要求模块名唯一。
`--explicit-package-bases` 也不解决（还是同名）。所以这个脚本**逐文件扫**。

## 用法

```bash
python scripts/lint/type_debt_tests.py           # 看现状
python scripts/lint/type_debt_tests.py --check   # 与预算不一致就退 1
```

逐文件扫 180 个文件约 2–3 分钟（8 线程）。这是"可以慢，但要能数清"的取舍。
"""

from __future__ import annotations

import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

#: `tests/unit` 当前的类型错误预算。
#:
#: **只许往下改**。修掉一批就把这个数字调小；加新测试带类型错误则必须当场修掉。
#: 起点 599（B577 实测）；B577 修 51 → 548；B578 修 41 → 507；B579 修 66 → 441；B580 修 57 → 384；B581 修 36 → 348；B582 修 34 → 314；B583 修 20 → 294；B585 修 19 → 275；B587 修 26 → 249；B589 修 26 → 218；B590 修 38 → 180；B591 修 21 → 159；B592 修 13 → 146；B593 修 29 → 117；B594 修 24 → 93；B595 修 26 → 67。
TESTS_ERROR_BUDGET = 67

#: 为什么不接进 `verify.py`：599 个错误要修很多批，一道注定红的闸门会训练人忽略 verify。
#: 等修到 0 再并入（与 `scripts/` 的路径相同：B570 立账 → B574 清零 → B576 并闸）。


def _count_one(path: Path) -> int:
    proc = subprocess.run(
        [sys.executable, "-m", "mypy", "--strict", str(path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=str(ROOT),
    )
    out = (proc.stdout or "") + (proc.stderr or "")
    # 汇总行 `Found N errors in M files` 不算
    return len([ln for ln in out.splitlines() if "error:" in ln and "Found " not in ln])


def count_errors(target: str = "tests/unit") -> tuple[int, dict[str, int]]:
    """逐文件扫（整目录扫会因同名 conftest 直接失败）。"""
    files = sorted((ROOT / target).glob("test_*.py"))
    with ThreadPoolExecutor(max_workers=8) as pool:
        counts = list(pool.map(_count_one, files))
    per_file = {
        str(f.relative_to(ROOT)): c for f, c in zip(files, counts) if c
    }
    return sum(counts), dict(sorted(per_file.items(), key=lambda kv: -kv[1]))


def main(argv: list[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    check = "--check" in args
    total, per_file = count_errors()
    print(f"tests/unit 类型错误：{total}（预算 {TESTS_ERROR_BUDGET}）")
    print(f"  涉及 {len(per_file)} 个文件")
    for path, n in list(per_file.items())[:8]:
        print(f"  {n:4}  {path}")
    if not check:
        print("\n（--check 才会因与预算不符退 1；现在只是看现状）")
        return 0
    if total != TESTS_ERROR_BUDGET:
        hint = "超预算：新加的测试请当场修掉类型错误。" if total > TESTS_ERROR_BUDGET \
            else f"欠账减少：请把 TESTS_ERROR_BUDGET 改成 {total}（只许往下）。"
        print(f"\n{hint}")
        return 1
    print("\n与预算一致。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
