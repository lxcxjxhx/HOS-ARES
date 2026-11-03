#!/usr/bin/env python3
"""B26 · 真模型驱动的 agent 主要功能测试。

这条命令回答一个问题：**接上真模型之后，判定链还站得住吗**。

它不做新判定，只把 `Loop` 的决策器从 `ScriptedDecider` 换成
`build_decider(client=from_env())`——有模型走模型，模型连续 3 次 schema
失败降级到 scripted（`08 §2`）。输出分两段：

1. **决策段**：真模型选了哪些工具、过没过窄 schema、第几步降级；
2. **判定段**：`verdicts` 与 `budget`——这部分由 oracle 决定，模型改不了。

判据是 `--compare-scripted`：同一条用例分别用真模型与纯 scripted 跑，
**共有 oracle 的 verdict 必须逐条相同**。覆盖差异（模型把步数花在探索上、
晚期 oracle 没跑到）不算冲突，但要分开打印——把"进度不同"说成"判定被改"
是最容易自欺的一种写法。

用法：

    export VERIPEN_MODEL_BASE_URL=...
    export VERIPEN_MODEL=...
    export VERIPEN_MODEL_API_KEY=...
    python scripts/b26/agent_live.py --max-steps 20 --compare-scripted
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
for _p in (ROOT, ROOT / "tests", ROOT / "tests" / "unit"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from conftest import Lab  # noqa: E402
from test_agent_loop import ORACLE_MAP, LabRuntime, _state  # noqa: E402

from veripen_cli._io import setup_stdout  # noqa: E402
from veripen_core.agent import AgentState, Decision, Loop, build_decider  # noqa: E402
from veripen_core.agent.client import from_env  # noqa: E402
from veripen_oracles import ALL_ORACLES  # noqa: E402
from veripen_oracles.common.harness import run as run_oracle  # noqa: E402

#: 一次运行的默认目标。写得像人在派活，不暗示任何 oracle 名字——
#: 模型只能从 `tools` 与 `state.pending` 自己判断该先验哪一条。
DEFAULT_GOAL = "验证 web-01 上真实存在的访问控制与注入缺陷，不要下结论，只选工具。"


class LiveRuntime(LabRuntime):
    """在测试夹具的 runtime 上补一处**真跑才会踩到**的缺口。

    ``LabRuntime.run_oracle`` 调 harness 时不传 ``bindings``——固定用例集里
    没有需要第二块面的 oracle，所以一直没暴露。真模型会自己挑 oracle，
    一旦挑到 ``bola_functional`` 就要求 ``normal_function`` 的注入，
    夹具只喂了一个绑定，于是抛 ``SymbolMissing`` 把整个循环打挂。

    这里不改夹具的判定逻辑，只把该给的东西给全：oracle 声明几个 selector_tag，
    就注入几条来自 lab 的绑定；失败如实记成 BLOCKED，不吞也不猜。
    """

    def run_oracle(self, oracle_id: str, route_ref: str, params: Any) -> dict[str, Any]:
        self.calls.append(oracle_id)
        cls = next((c for c in ALL_ORACLES if c.meta.id == oracle_id), None)
        if cls is None:
            return {"verdict": "BLOCKED", "reason": "unknown_oracle", "route_strength": "contract"}
        oracle = cls()
        tags = [str(t) for t in getattr(oracle.meta, "selector_tags", ())]
        bindings: dict[str, Any] = {}
        for tag in tags:
            try:
                bindings[tag] = self.lab.binding(tag)
            except LookupError:
                continue
        if not tags or tags[0] not in bindings:
            return {"verdict": "BLOCKED", "reason": "route_unresolved", "route_strength": "contract"}
        try:
            result = run_oracle(
                oracle,
                bindings[tags[0]],
                self.lab.transport,
                symbols=self.lab.symbols,
                nonce=f"live-{oracle_id}",
                token=self.lab.token,
                bindings=bindings,
            )
        except Exception as exc:  # noqa: BLE001 - 如实报告，不吞
            return {
                "verdict": "BLOCKED",
                "reason": f"{type(exc).__name__}: {exc}"[:120],
                "route_strength": "contract",
            }
        return {
            "verdict": result.verdict.value,
            "reason": result.reason,
            "route_strength": "contract",
            "capsule_id": f"sha256:{oracle_id}" if result.verdict.value == "VERIFIED" else None,
        }


def _run(lab: Lab, decider: Any, state: AgentState, goal: str, max_steps: int) -> dict[str, Any]:
    runtime = LiveRuntime(lab, ORACLE_MAP)
    t0 = time.perf_counter()
    result = Loop(runtime=runtime, decider=decider, goal=goal, max_steps=max_steps).run(state)
    return {
        "decision": result.decision.value,
        "reason": result.reason,
        "verdicts": dict(result.verdicts),
        "budget": dict(result.budget),
        "steps": [
            {
                "step": e.step,
                "tool": e.tool,
                "ok": e.ok,
                "error_class": e.error_class,
                "new_facts": e.new_facts,
                "verdict": e.verdict,
            }
            for e in result.step_entries
        ],
        "runtime_calls": list(runtime.calls),
        "rediscovery": runtime.rediscovery,
        "wall_ms": round((time.perf_counter() - t0) * 1000, 1),
    }


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="agent-live")
    ap.add_argument("--max-steps", type=int, default=20,
                    help="两边给同样的步数；模型前几步常花在探索上")
    ap.add_argument("--goal", default=DEFAULT_GOAL)
    ap.add_argument("--compare-scripted", action="store_true",
                    help="同一条用例再用纯 scripted 跑一次，比对共有 oracle 的 verdict")
    ap.add_argument("--out", default=str(ROOT / "out" / "b26"))
    args = ap.parse_args(argv)
    setup_stdout()

    client = from_env()
    if client is None:
        print("模型未配置：需要 VERIPEN_MODEL_BASE_URL / VERIPEN_MODEL / VERIPEN_MODEL_API_KEY",
              file=sys.stderr)
        return 2

    live_decider = build_decider(client=client)
    state = _state()
    print(f"模型：{client.model} @ {client.base_url}")
    print(f"决策器：{live_decider.name}")
    print(f"用例：{len(state.pending)} 个 oracle，上限 {args.max_steps} 步")
    print("-" * 78)

    live = _run(Lab(secure=False), live_decider, state, args.goal, args.max_steps)
    for s in live["steps"]:
        flag = "ok " if s["ok"] else "ERR"
        verdict = f" verdict={s['verdict']}" if s["verdict"] else ""
        print(f"  {s['step']:>2} {flag} {s['tool']:<20} facts+{s['new_facts']}{verdict}")
    print("-" * 78)
    print(f"决策：{live['decision']}（{live['reason']}）")
    print(f"判定：{json.dumps(live['verdicts'], ensure_ascii=False)}")
    print(f"预算：{json.dumps({k: v for k, v in live['budget'].items() if v}, ensure_ascii=False)}")
    live_usage = dict(client.usage)
    print(f"耗时：{live['wall_ms']} ms")
    print(
        f"模型调用：{live_usage['calls']} 次 · "
        f"prompt {live_usage['prompt_tokens']} / "
        f"completion {live_usage['completion_tokens']} tokens"
    )

    record: dict[str, Any] = {
        "model": client.model, "goal": args.goal, "live": live,
        "live_usage": live_usage,
    }
    problems: list[str] = []

    if args.compare_scripted:
        scripted = _run(Lab(secure=False), build_decider(client=None), state, args.goal, args.max_steps)
        record["scripted"] = scripted

        shared = sorted(set(live["verdicts"]) & set(scripted["verdicts"]))
        conflict = {
            k: [live["verdicts"][k], scripted["verdicts"][k]]
            for k in shared
            if live["verdicts"][k] != scripted["verdicts"][k]
        }
        only_live = sorted(set(live["verdicts"]) - set(scripted["verdicts"]))
        only_scripted = sorted(set(scripted["verdicts"]) - set(live["verdicts"]))
        record["compare"] = {
            "shared": shared,
            "conflict": conflict,
            "only_live": only_live,
            "only_scripted": only_scripted,
        }

        print()
        print(f"对照 scripted：scripted {len(scripted['verdicts'])} 条 / live {len(live['verdicts'])} 条")
        print(f"  共有判定 {len(shared)} 条，结论冲突 {len(conflict)} 条")
        if only_scripted:
            print(f"  仅 scripted 覆盖（步数未到）：{only_scripted}")
        if only_live:
            print(f"  仅 live 覆盖：{only_live}")
        if conflict:
            problems.append(f"模型改了判定：{conflict}")
        if not shared:
            problems.append("两边没有任何一条共有判定，对照没有意义（步数太少）")

    schema_failures = sum(1 for s in live["steps"] if s["error_class"] == "schema")
    record["schema_failures"] = schema_failures
    if schema_failures:
        print(f"schema 失败：{schema_failures} 次（≤3 次属自愈范围内）")

    if live["decision"] == Decision.NOTHING_TO_DO.value:
        problems.append("循环一步未跑：state 为空")

    record["problems"] = problems
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "agent_live.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print()
    print(f"证据：{out / 'agent_live.json'}")
    for p in problems:
        print(f"[!] {p}")
    return 0 if not problems else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
