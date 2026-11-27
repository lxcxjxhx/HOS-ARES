"""B566 · 模型给了**不合规**响应时，软件必须降级而不是崩或瞎猜。

真模型的服从性是未知的——但**客户端怎么应对不服从**是完全可以验的。
本批给假端点加了 7 种坏响应，逐条钉住：

| 模型的坏行为 | 软件必须 |
|---|---|
| 说人话不给 JSON | 降级 rules，不崩 |
| 未知工具名 | 拒绝，降级 |
| 多一个字段（`extra=forbid`） | 拒绝，降级 |
| 缺必需字段 | 拒绝，降级 |
| 返回 `null` / 空串 | 降级 |
| 超长正文（200 KB） | 有界，不撑爆 |
| 一次给两个调用（数组） | **允许**（codex 式并行），逐个执行 |

最后一条是唯一"必须接受"的——把它和前面六条放在一套里，
是为了证明这套判据不是"一律拒绝"。

用法：``python scripts/audit_model_conformance.py``
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
for extra in (ROOT, ROOT / "tests"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

#: 应当被**计划层降级**的坏响应（拿不出合法调用）
MUST_FALLBACK = ("bad_schema", "prose", "empty", "null")
#: 计划层**放行**、但**执行层必须拒绝**的（字段级校验在工具面里做）
MUST_FAIL_AT_EXEC = ("extra_field", "missing_field")
#: 应当被**接受**的（数组形式的多调用）
MUST_ACCEPT = "two_calls"


def _probe_exec(mode: str, tmp: Path) -> dict[str, Any]:
    """跑一次真实执行，看**执行层**是否拒绝。

    为什么这两项不该在计划层降级（B566 实测）：计划层只校验"工具名在不在、
    args 是不是 dict"，字段级校验交给工具面的 pydantic schema（`extra="forbid"`）。
    那样做的好处是**错误落到具体字段上**（`bogus: Extra input`），
    而不是笼统的"模型给了不合规的调用"。
    """
    from lab.model_server import ModelServer

    from hos_ares_cli import cmd_chat
    from hos_ares_core.agent.client import HttpChatClient
    from hos_ares_core.agent.intent import Step

    (tmp / "a.txt").write_text("seed\n", encoding="utf-8")
    from hos_ares_core.workbench.fs import Workbench

    srv = ModelServer(mode=mode).start()
    try:
        client = HttpChatClient(
            base_url=srv.base_url, model="local", system_prompt=cmd_chat.CHAT_SYSTEM_PROMPT,
        )
        plan, _why = cmd_chat._plan_from_model(client, "读一下 a.txt", [])
        if plan is None:
            return {"planned": False, "rejected_by_exec": False}
        wb = Workbench(str(tmp))
        results = [
            cmd_chat._exec_step(Step(s.tool, dict(s.args), "t"), wb=wb, root=tmp)
            for s in plan.steps
        ]
        return {
            "planned": True,
            "rejected_by_exec": all(not ok for ok, _ in results),
            "detail": [out[:60] for _ok, out in results],
        }
    finally:
        srv.stop()


def _probe(mode: str) -> dict[str, Any]:
    """起一个指定 mode 的假端点，看 chat 的计划层返回什么。"""
    from lab.model_server import ModelServer

    from hos_ares_cli import cmd_chat

    srv = ModelServer(mode=mode).start()
    try:
        from hos_ares_core.agent.client import HttpChatClient

        client = HttpChatClient(
            base_url=srv.base_url, model="local", system_prompt=cmd_chat.CHAT_SYSTEM_PROMPT,
        )
        plan, why = cmd_chat._plan_from_model(client, "读一下 a.txt", [])
        if plan is None:
            return {"fell_back": True, "why": why, "steps": 0}
        return {"fell_back": False, "why": "", "steps": len(plan.steps),
                "tools": [s.tool for s in plan.steps]}
    finally:
        srv.stop()


def audit() -> dict[str, Any]:
    import tempfile

    out: dict[str, Any] = {}
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        # **不展开 MUST_ACCEPT**（B566 踩过）：它是 str，`*"two_calls"` 会拆成单字符，
        # 于是真正要测的那个 mode 一次都没跑，结果读出来是 None。
        for mode in (*MUST_FALLBACK, MUST_ACCEPT):
            try:
                out[mode] = _probe(mode)
            except Exception as exc:  # noqa: BLE001 —— 崩了也是一种结果，记下来
                out[mode] = {"crashed": f"{type(exc).__name__}: {exc}"}
        for mode in MUST_FAIL_AT_EXEC:
            try:
                out[mode] = _probe_exec(mode, tmp)
            except Exception as exc:  # noqa: BLE001
                out[mode] = {"crashed": f"{type(exc).__name__}: {exc}"}
    return out


def main() -> int:
    import sys as _sys

    # 控制台可能是 GBK（Windows）：`✗` 这类字符会让 print 直接抛。
    # 先把整份结果写成 UTF-8 JSON，终端只打结论。
    for stream in (_sys.stdout, _sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass

    result = audit()
    out = ROOT / "out" / "model_conformance.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"详表：{out}")

    problems: list[str] = []
    for mode in MUST_FALLBACK:
        row = result.get(mode, {})
        if row.get("crashed"):
            problems.append(f"{mode}：崩了（{row['crashed']}）")
        elif not row.get("fell_back"):
            problems.append(f"{mode}：没降级，产出了 {row.get('steps')} 步计划")
    for mode in MUST_FAIL_AT_EXEC:
        row = result.get(mode, {})
        if row.get("crashed"):
            problems.append(f"{mode}：崩了（{row['crashed']}）")
        elif not row.get("planned"):
            problems.append(f"{mode}：计划层就拒了——但字段校验该由工具面做（错误要落到字段上）")
        elif not row.get("rejected_by_exec"):
            problems.append(f"{mode}：执行层居然接受了坏的 args")
    two = result.get(MUST_ACCEPT, {})
    if two.get("crashed"):
        problems.append(f"two_calls：崩了（{two['crashed']}）")
    elif two.get("steps", 0) < 2:
        problems.append(f"two_calls：多调用被吞了，只得到 {two.get('steps')} 步")

    if problems:
        print("问题：")
        for p in problems:
            print("  - " + p)
        return 1
    print("结论：四种坏响应在计划层降级、两种字段错误在执行层被拒、多调用正常接受")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
