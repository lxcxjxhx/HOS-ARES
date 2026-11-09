#!/usr/bin/env python3
"""ledger-check —— 批次账本的诚实性闸门。

规则，针对"标了 done 但实际不完整"这一类失真：

1. **done 的批次 open 里不许出现 `not implemented` / `不存在` 这类"缺件"描述**
   —— 缺件不是遗留问题，是没做完。
2. **`done(legacy-scope)` 必须带 scope_note** —— 说明它按哪一版口径完成，
   否则读者无法判断"done 到底覆盖了什么"。
3. **`scaffolded` 必须带 `scaffold_note` 与 `owner_batch`**（v3.6 / `39 §2.2` I14.1）
   —— 形状齐但注入点空的批次只能标这个状态，且必须点名谁接；
   它**不计入完成分母**，所以声明归属是硬要求，否则等于把缺口挂起来没人认。

用法：python scripts/lint/ledger_check.py [--json]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LEDGER = ROOT / ".veripen" / "batches.jsonl"

#: 命中即"这是缺件，不是遗留"
MISSING_MARKERS = ("不存在", "not implemented", "未实现", "尚未实现", "缺表", "缺列")

#: 允许的状态值。`scaffolded` 是 v3.6 新增（I14）：形状齐、注入点空，不计完成分母。
STATES = {"todo", "doing", "done", "done(legacy-scope)", "scaffolded", "blocked", "planned"}


def check(rows: list[dict], root: Path) -> tuple[list[str], list[str]]:
    """返回 (problems, stale)。**problems 是硬错，stale 只是提醒**（B178）。

    stale = 历史条目引用的文件现在不存在。它**不**说明那条记录写错了：
    后续批次可能删/改名了它（实测：B123 的 `test_csp_oracle.py` 被后来的
    "响应头三件套"批次删掉）。所以只提醒，不算错。
    """
    problems: list[str] = []
    stale: list[str] = []
    #: 批次号唯一；唯一例外是"补记"行——它必须同时带 supersedes_state 与 correction
    seen: set[str] = set()

    for row in rows:
        batch, state = row.get("batch", "?"), row.get("state", "")
        if batch in seen:
            if not (row.get("supersedes_state") and row.get("correction")):
                problems.append(
                    f"{batch}: 批次号重复且不是补记行（补记要带 supersedes_state + correction）"
                )
        seen.add(batch)
        if state not in STATES:
            problems.append(f"{batch}: 未知状态 {state!r}")
        if state.startswith("done") and not row.get("dod"):
            problems.append(f"{batch}: done 但没有 DoD 证据")
        for name in row.get("files") or []:
            if not (root / str(name)).exists():
                stale.append(f"{batch}: {name}")
        # 缺件描述只在 **done** 类状态里算问题：done 却写着"不存在/未实现"，
        # 说明它没做完。blocked / scaffolded 本来就是要写"缺什么"的状态，
        # 对它们检查这条等于禁止记录阻塞原因。
        if state.startswith("done"):
            for item in row.get("open", []):
                if any(marker in item for marker in MISSING_MARKERS):
                    problems.append(
                        f"{batch}: open 里有缺件描述（应改状态或补实现）：{item[:60]}"
                    )
        if state == "done(legacy-scope)" and not row.get("scope_note"):
            problems.append(f"{batch}: done(legacy-scope) 缺 scope_note")
        # v3.6 / I14：scaffolded 必须点名谁接、卡在哪
        if state == "scaffolded":
            if not row.get("scaffold_note"):
                problems.append(f"{batch}: scaffolded 缺 scaffold_note（形状齐在哪、空缺在哪）")
            if not row.get("owner_batch"):
                problems.append(f"{batch}: scaffolded 缺 owner_batch（谁接回 done，不许悬空）")

    return problems, stale


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    rows = [
        json.loads(line)
        for line in LEDGER.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    problems, stale = check(rows, ROOT)

    if args.json:
        print(json.dumps(
            {"total": len(rows), "problems": problems, "stale": stale},
            ensure_ascii=False, indent=2,
        ))
    else:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        for p in problems:
            print(f"LEDGER {p}")
        if stale:
            print(f"提醒：{len(stale)} 条历史条目引用的文件现在不存在（可能被后续批次删/改名）")
            for s in stale[:10]:
                print(f"  · {s}")
        print(f"ledger-check: batches={len(rows)} problems={len(problems)}")
    return 0 if not problems else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
