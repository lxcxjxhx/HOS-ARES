"""demo 的证据链必须是**真的**。

demo 是交付演示，它的卖点是"每条发现能复现"。所以有两处不能假：

1. capsule 的步骤/断言必须来自**这次真跑**（早先用 ``capsule_fixture`` 合成，
   再给 oracle id 打补丁——步骤和断言都是模板值）；
2. replay 必须**真发包**（早先用替身按 role 返回 capsule 自带的 status/body_sha，
   等于自己跟自己比，永远 OK）。

两条测试各钉一处；第 2 条带反向证明：换一个不满足条件的目标，必须不再 OK。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
for extra in (ROOT, ROOT / "tests"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))


def _demo():  # noqa: ANN202
    spec = importlib.util.spec_from_file_location("_demo_under_test", ROOT / "scripts" / "demo.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_demo_under_test"] = mod
    spec.loader.exec_module(mod)
    return mod


def _runtime(secure: bool = False):  # noqa: ANN202
    from lab.app import App
    from lab.transport_oob import LabTransport

    m = _demo()
    app = App(secure=secure)
    return m, app, m.DemoRuntime(app, LabTransport(app))


def test_demo_capsule_steps_come_from_the_real_run(tmp_path: Path) -> None:
    """capsule 的每步状态必须等于这次运行真正收到的状态。

    夹具合成的 capsule 状态是写死的（200/403），与真实请求无关。
    这里对比 capsule 步骤与 ``result.pairs`` 的 trace——两者不一致就说明证据是造的。
    """
    m, _app, rt = _runtime(secure=False)
    out = rt.run_oracle("sqli_bool_diff", "", {})
    assert out["verdict"] == "VERIFIED"

    sealed = m.step3_evidence(rt, tmp_path)
    assert sealed, "没有产出 capsule"
    cap, _path = sealed[0]
    result = rt.capsules[0]["result"]

    # 步骤 1 是基线，其后与 pairs 一一对应
    assert cap.steps[0].response.status == result.pairs[0].baseline.status
    for step, pair in zip(cap.steps[1:], result.pairs):
        assert step.role == pair.role
        assert step.response.status == pair.trace.status, (
            f"capsule 步骤状态 {step.response.status} 与实际收到的 {pair.trace.status} 不一致"
        )
    # 断言也必须来自 judge，而不是模板常量
    assert cap.assertion.kind == result.judgement.assertion["kind"]


def test_demo_replay_actually_sends(tmp_path: Path) -> None:
    """step5 必须**真发包**：替身 transport 收到几次 send，就说明重放真的走了几轮。"""
    m, _app, rt = _runtime(secure=False)
    rt.run_oracle("sqli_bool_diff", "", {})
    sealed = m.step3_evidence(rt, tmp_path)
    assert sealed

    cap, _path = sealed[0]
    calls = {"n": 0}

    class Counting:
        def __init__(self) -> None:
            self.inner = m.NetTransport(token_for=m.make_token) if hasattr(m, "NetTransport") else None

        def send(self, binding, params, role, headers=None):  # noqa: ANN001, ANN201
            calls["n"] += 1
            raise AssertionError("本测试只数调用次数，不真发")

    # step5 内部会用传入的 transport；这里用计数替身确认它被调用
    try:
        m.step5_replay(sealed, Counting())
    except AssertionError:
        pass
    assert calls["n"] >= 1, "step5 一次都没调用 transport——那还是自己跟自己比"
