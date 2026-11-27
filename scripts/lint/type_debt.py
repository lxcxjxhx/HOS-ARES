"""`scripts/` 的类型检查欠账台账（B570）。

## 为什么需要这个文件

`scripts/verify.py` 的 `MYPP_TARGETS` 只覆盖 **6 个产品目录**：

```
hos_ares_core/  worker/  hos_ares_oracles/  hos_ares/  hos_ares_cli/  web/
```

`tests/` 与 `scripts/` **不在其中**。后果在 B569 实测过一次：
我给用例用了 `plugins_pkg.Registry = ...`，`mypy --strict` 报
`Cannot assign to a type`，而 `verify.py` 是**绿的**——它根本没看那个文件。

## 为什么不一刀切全扫

`mypy --strict scripts/` 实测 **74 个错误、分布在 10+ 个文件**。
一次性全修风险远大于收益（都是开发脚本，不进产品路径），
但"不知道有多少"是不可接受的——所以这里把数字立成台账。

## 用法

```bash
python scripts/lint/type_debt.py          # 只看现状
python scripts/lint/type_debt.py --check  # 超过预算就退 1（接闸门用）
```

这个脚本**不进 `verify.py` 的阻塞闸门**，理由写在最后一行常量里。
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

#: `scripts/` 当前的类型错误预算。
#:
#: **它只许往下改**：修掉一批就把这个数字调小；加新脚本引入错误则必须当场修掉，
#: 否则 `--check` 会红。这样欠账不会无声增长。
SCRIPT_ERROR_BUDGET = 33

#: 为什么不接进 `verify.py`：那 33 个错误全在开发脚本里（demo / bench / 一次性工具），
#: 与产品路径无关。把一道"注定红"的闸门塞进 `verify.py` 只会让人开始忽略 verify 的输出——
#: 那比没有闸门更糟。等修到 0 再并入主闸门。


def count_errors(target: str = "scripts/") -> tuple[int, list[str]]:
    """跑 mypy 数**只属于该目录**的错误（不算顺带追进来的 tests/?）。

    路径分隔符要归一化：Windows 上 mypy 输出 `scripts\\xxx.py:12: error: ...`，
    而比较时用的是 `/`。早先没归一化，数出来恒为 0——**量具自己骗自己**（B570 踩过）。
    """
    proc = subprocess.run(
        [sys.executable, "-m", "mypy", "--strict", target],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=str(ROOT), timeout=900,
    )
    out = (proc.stdout or "") + (proc.stderr or "")
    prefix = target.replace("/", "\\") if "\\" in out else target
    real: list[str] = []
    for ln in out.splitlines():
        if "error:" not in ln:
            continue
        # 汇总行 `Found N errors in M files` 没有冒号+数字的前缀，天然被上面排掉
        head = ln.split(":")[0]
        if head.startswith(prefix) or head.startswith(target.rstrip("/")):
            real.append(ln)
    return len(real), real


def by_file(lines: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for ln in lines:
        path = ln.split(":")[0]
        counts[path] = counts.get(path, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: -kv[1]))


def main(argv: list[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    check = "--check" in args
    total, lines = count_errors()
    print(f"scripts/ 类型错误：{total}（预算 {SCRIPT_ERROR_BUDGET}）")
    for path, n in list(by_file(lines).items())[:8]:
        print(f"  {n:3}  {path}")
    if not check:
        print("\n（--check 才会因超预算退 1；现在只是看现状）")
        return 0
    if total > SCRIPT_ERROR_BUDGET:
        print(f"\n超预算：{total} > {SCRIPT_ERROR_BUDGET}。"
              "新加的脚本不许带类型错误——要么修掉，要么把预算调小后重跑。")
        return 1
    if total < SCRIPT_ERROR_BUDGET:
        print(f"\n欠账减少：{total} < {SCRIPT_ERROR_BUDGET}。"
              f"请把 `SCRIPT_ERROR_BUDGET` 改成 {total}（只许往下）。")
        return 1          # 减少也要红：逼着把预算调小，否则预算形同虚设
    print("\n与预算一致。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
