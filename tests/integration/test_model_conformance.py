"""B566 · 模型不服从时的**七种收场**，逐条钉住。

真模型会不会守窄 schema 是未知的——但**客户端怎么应对不服从**完全可以验。
本批给假端点加 6 种坏响应，覆盖三层收场：

| 收场层 | mode | 软件必须 |
|---|---|---|
| 计划层降级 | `prose`（说人话） | 退回 rules，不崩 |
| 计划层降级 | `empty` / `null` | 同上 |
| 计划层降级 | `bad_schema`（未知工具） | 同上，且说清原因 |
| **执行层拒绝** | `extra_field` | 错误落到**字段**上（`Extra input`） |
| **执行层拒绝** | `missing_field` | 错误落到**字段**上（`Field required`） |
| **正常接受** | `two_calls`（数组） | 两个调用都执行（codex 式） |

最后一条是"必须接受"的——把它和前面放在一套里，证明判据不是"一律拒绝"。

## 顺带修的两处产品缺陷（B566）

1. `_extract_payload` 把**顶层数组**当"多个对象"拒掉 → `cmd_chat` 里那个
   `isinstance(raw, (list, tuple))` 分支**永远走不到**；
2. `HttpChatClient.__call__` 末尾 `return dict(parsed)` 会把 list 转坏。

两处各自都"对"，加起来是不可达代码。
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
for extra in (ROOT, ROOT / "tests"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

PROMPT = "读一下 a.txt"


def _plan(mode: str) -> tuple[Any, str]:
    from lab.model_server import ModelServer

    from hos_ares_cli import cmd_chat
    from hos_ares_core.agent.client import HttpChatClient

    srv = ModelServer(mode=mode).start()
    try:
        client = HttpChatClient(
            base_url=srv.base_url, model="local", system_prompt=cmd_chat.CHAT_SYSTEM_PROMPT,
        )
        return cmd_chat._plan_from_model(client, PROMPT, [])
    finally:
        srv.stop()


# ── 1：计划层必须降级的四种 ─────────────────────────────────
@pytest.mark.parametrize("mode", ["prose", "empty", "null", "bad_schema"])
def test_bad_responses_fall_back_to_rules(mode: str) -> None:
    """拿不出合法调用 → 降级 rules，**不崩、不猜**。"""
    plan, why = _plan(mode)
    assert plan is None, f"{mode} 居然产出了 {plan}"
    assert why, "降级必须给原因"


def test_prose_reason_quotes_what_the_model_said() -> None:
    """说人话时，错误里要**带出原文**——人要能看见模型到底说了什么。"""
    _plan_result, why = _plan("prose")
    assert "README" in why or "建议" in why, why


# ── 2：多调用必须被接受（codex 式）───────────────────────────
def test_two_calls_are_accepted() -> None:
    """**这是本批唯一"必须接受"的判据**：数组形式的多调用要能通过计划层。

    早先 `_extract_payload` 把顶层数组当"多个对象"拒掉，
    于是 `_plan_from_model` 里那个 `isinstance(raw, (list, tuple))` 分支
    **永远走不到**——两层各自都"对"，加起来是不可达代码。
    """
    plan, why = _plan("two_calls")
    assert plan is not None, f"多调用被拒了：{why}"
    assert len(plan.steps) == 2, plan.steps
    assert [s.tool for s in plan.steps] == ["read", "write"]


# ── 3：字段级错误在执行层被拒（错误要落到字段上）────────────
@pytest.mark.parametrize("mode,needle", [("extra_field", "Extra"), ("missing_field", "required")])
def test_field_errors_are_rejected_at_execution(mode: str, needle: str, tmp_path: Path) -> None:
    """多给/缺字段 → **执行层**拒绝，且错误指向具体字段。

    为什么不在计划层就降级：计划层只校验"工具名在不在、args 是不是 dict"，
    字段级交给工具面的 pydantic schema。好处是错误能落到 `bogus: Extra input`
    这种具体位置上，而不是笼统的"模型给了不合规的调用"。
    """
    from hos_ares_cli import cmd_chat
    from hos_ares_core.agent.intent import Step
    from hos_ares_core.workbench.fs import Workbench

    (tmp_path / "a.txt").write_text("seed\n", encoding="utf-8")
    plan, why = _plan(mode)
    assert plan is not None, f"{mode} 不该在计划层被拒：{why}"

    wb = Workbench(str(tmp_path))
    results = [
        cmd_chat._exec_step(Step(s.tool, dict(s.args), "t"), wb=wb, root=tmp_path)
        for s in plan.steps
    ]
    assert all(not ok for ok, _ in results), results
    assert any(needle in out for _ok, out in results), [o for _k, o in results]


# ── 4：正常响应仍然通（排除"一律拒绝"）──────────────────────
def test_a_clean_response_still_works() -> None:
    """前面那么多"必须拒"，得有一条证明**正常的仍然通**。"""
    plan, why = _plan("ok")
    assert plan is not None, why
    assert len(plan.steps) >= 1
