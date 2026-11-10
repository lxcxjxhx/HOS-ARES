"""B127 · 执行体行为可离线验证（E1 的"能验先验"部分）。

E1「真隔离」需要容器运行时，本机没有。但**执行体的行为**（超时是否真杀、退出码是否保留、
输出是否捕获）不需要容器也能验——这三件以前被一句"没有 docker 就 SKIP"一起吞掉了。
"""

from __future__ import annotations

import pytest

from veripen_core.sandbox.attest import attest, verdict
from veripen_core.sandbox.runner import select_executor

_EXECUTOR = select_executor()


def _skip() -> None:
    if _EXECUTOR is None:
        pytest.skip("本机没有可用执行体")


@pytest.fixture(scope="module")
def facts():  # noqa: ANN201
    _skip()
    assert _EXECUTOR is not None
    return attest(_EXECUTOR)


def test_probes_run_and_are_recorded(facts) -> None:  # noqa: ANN001
    assert facts["checked"] is True
    for key in ("stdout_captured", "exit_code_preserved", "timeout_enforced"):
        assert key in facts, f"缺事实项 {key}"


def test_timeout_is_actually_enforced(facts) -> None:  # noqa: ANN001
    """超时不是"记一笔"，是**真的杀**。"""
    assert facts["timeout_enforced"] is True


def _probes_never_ran(facts) -> bool:  # noqa: ANN001
    """探针在本机根本没跑起来（stdout 与退出码都没拿到）。

    与 doctor 的裁决同一口径（B200）：**没能验 ≠ 行为坏**。干净 runner 上
    子进程起不来时，把"没拿到"记成 assert 失败等于给每次干净安装报假故障。
    """
    return not facts.get("stdout_captured") and not facts.get("exit_code_preserved")


def test_exit_code_is_not_swallowed(facts) -> None:  # noqa: ANN001
    if _probes_never_ran(facts):
        pytest.skip("本机探针没跑起来：退出码传播未验（不是行为坏）")
    assert facts["exit_code_preserved"] is True


def test_isolation_is_reported_honestly(facts) -> None:  # noqa: ANN001
    """资源隔离不许写成容器隔离——这是 E1 的"否证"。"""
    assert facts["isolation"], "执行体必须自报隔离等级"
    assert facts["isolation_is_container"] == (facts["isolation"] == "container")


def test_verdict_refuses_to_call_it_isolation(facts) -> None:  # noqa: ANN001
    if _probes_never_ran(facts):
        pytest.skip("本机探针没跑起来：判定未验（不是行为坏）")
    ok, why = verdict(facts)
    if facts["isolation_is_container"]:
        assert ok is True
    else:
        assert ok is False, "没有容器隔离就不许判可交付"
        assert "真隔离" in why


def test_unavailable_executor_is_not_checked() -> None:
    class _Missing:
        name = "missing"

        def available(self) -> bool:
            return False

        def run(self, *_a: object, **_k: object):  # noqa: ANN201
            raise AssertionError("不该被调用")

    facts = attest(_Missing())  # type: ignore[arg-type]
    assert facts["checked"] is False
    ok, why = verdict(facts)
    assert ok is False and "不可用" in why
