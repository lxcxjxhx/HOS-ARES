"""B179：默认排除表里写了一个不存在的 oracle —— 压力类从来没被关掉。

判据：

```
DEFAULT_OFF（policy）      = {mass_assignment, rate_limit_absent, xss_stored_probe}
run 的 DEFAULT_EXCLUDE     = rate_limit_missing, mass_assignment, xss_stored_probe
                             ^^^^^^^^^^^^^^^^^^ 没有这个 oracle（真名 rate_limit_absent）
hunt 的默认排除             = rate_limit_absent, mass_assignment, xss_stored_probe  ← 是对的
```

后果：只要某条面被打上 `rate_sensitive`，`run` 会**默认**去跑 `rate_limit_absent`
（压力类动作，DEFAULT_OFF 里明确关着它）。排除表里写错一个字母，策略就漏一条。

这组用例钉的是**不变量**，不是具体字符串：
1. 默认排除表里的每个 id 都必须是真 oracle（拼错即红）；
2. DEFAULT_OFF 必须被默认排除覆盖（否则"默认关"是空话）；
3. run 与 hunt 的默认排除必须同口径。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for extra in (ROOT, ROOT / "tests"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))


def _ids() -> set[str]:
    from veripen_oracles import ALL_ORACLES

    return {c.meta.id for c in ALL_ORACLES}


def _split(raw: str) -> set[str]:
    return {x.strip() for x in raw.split(",") if x.strip()}


def test_run_default_exclude_has_no_typos() -> None:
    from veripen_cli.cmd_run import DEFAULT_EXCLUDE

    unknown = _split(DEFAULT_EXCLUDE) - _ids()
    assert not unknown, f"默认排除里写了不存在的 oracle：{sorted(unknown)}"


def test_default_off_is_covered_by_the_default_exclude() -> None:
    from veripen_cli.cmd_run import DEFAULT_EXCLUDE
    from veripen_oracles.policy import DEFAULT_OFF

    missing = set(DEFAULT_OFF) - _split(DEFAULT_EXCLUDE)
    assert not missing, f"这些默认关闭的类没被默认排除：{sorted(missing)}"


def test_run_and_hunt_share_the_same_default_exclude() -> None:
    """同一份口径写在两处，就必须一致——不一致时至少有一处是错的。"""
    import re

    from veripen_cli.cmd_run import DEFAULT_EXCLUDE

    hunt_src = (ROOT / "veripen_cli" / "cmd_hunt.py").read_text(encoding="utf-8")
    match = re.search(
        r'"--exclude",\s*\n?\s*default="([a-z_,]+)"', hunt_src
    )
    assert match, "hunt 的默认排除没找到（形态变了就更新这条用例）"
    assert _split(match.group(1)) == _split(DEFAULT_EXCLUDE), (
        f"run={sorted(_split(DEFAULT_EXCLUDE))} vs hunt={sorted(_split(match.group(1)))}"
    )


def test_rate_limit_absent_is_the_real_id() -> None:
    """把当年的错法写进用例：`rate_limit_missing` 不存在。"""
    assert "rate_limit_absent" in _ids()
    assert "rate_limit_missing" not in _ids()
