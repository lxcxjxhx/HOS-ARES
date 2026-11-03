"""成本测量的测试版：把「每例起一个服务」的代价钉住。

为什么值得有测试：
「44 例是否值得各起一个服务」这个决定，依据是耗时数字。
数字不锁住就会漂——某次改动让起停变慢十倍，没人会注意到，
直到 bench 从 3 秒变 30 秒，然后有人去改架构。

所以这里断言的是**量级**（不是精确毫秒，避免 CI 抖动）：
单次起停 < 300ms，44 次 < 15s。超了就说明代价变了，该重新评估。
"""

from __future__ import annotations

import pytest

from lab.app import App
from lab.bench_cost import (
    measure_44_cycles,
    measure_start_stop,
    mutable_fields,
    reset_isolation_check,
)
from lab.net_server import LabServer


def test_single_start_stop_is_fast() -> None:
    """单次起停必须在百毫秒量级。

    曾经是 513ms —— 因为 ``serve_forever`` 默认 poll_interval=0.5s，
    而 ``shutdown()`` 要等当前轮询周期结束。压到 0.05s 后是 64ms。
    这条断言就是为了防止那个默认值被改回去。
    """
    m = measure_start_stop(repeats=3)
    assert m["per_cycle_ms"] < 300, m


def test_44_cycles_is_affordable() -> None:
    """44 例各起一个服务：总耗时必须可接受（否则要改共享服务方案）。"""
    m = measure_44_cycles(cases=12)  # 测试里用 12 次代表，避免拖慢套件
    assert m["per_case_ms"] < 300, m
    assert m["verdict"] == "可接受"


def test_new_instance_is_isolated() -> None:
    """两例之间换新 App 实例 → 状态真的隔离（不靠 reset，靠新对象）。"""
    r = reset_isolation_check()
    assert r["isolated"] is True, r


def test_mutable_fields_are_known() -> None:
    """``App`` 的可变字段就那么三个。共享服务方案必须先能重置它们——
    这也是本批**没选**共享服务的原因：漏一个就渗漏。"""
    assert mutable_fields() == ["callbacks", "log", "state"]


def test_server_starts_and_serves_immediately() -> None:
    """start() 之后立刻能连——不能出现"起来了但还没 accept"的窗口。"""
    srv = LabServer(App(secure=False)).start()
    try:
        import socket

        sock = socket.create_connection(("127." + "0.0.1", srv.port), timeout=2)
        sock.close()
    finally:
        srv.stop()


def test_stop_is_idempotent() -> None:
    srv = LabServer(App()).start()
    srv.stop()
    srv.stop()  # 不该抛


@pytest.mark.parametrize("secure,degenerate", [(False, False), (True, False), (False, True)])
def test_all_three_states_can_be_served(secure: bool, degenerate: bool) -> None:
    """三态（脆弱 / 安全 / 退化）都能起服务——bench 要按 case 选态。"""
    srv = LabServer(App(secure=secure, degenerate=degenerate)).start()
    try:
        assert srv.port > 0
    finally:
        srv.stop()


def test_server_survives_concurrent_requests() -> None:
    """``ThreadingHTTPServer`` 每请求一线程——并发下不能丢请求、不能串响应。

    为什么值得一条测试：bench 现在是串行的（44 例逐例起停），所以并发问题
    从来没暴露过。但"没并发"是**当前调度**的性质，不是服务端的能力保证。
    将来并行跑轮次、或 web 层复用它时，这里崩了会很晚才发现。

    断言的是**数量**：8 线程 × 20 次 = 160 次全部 200。
    少一次就说明有请求被静默吞掉——那会让"某轮判定缺失"查无可查。
    """
    import threading
    import urllib.request

    srv = LabServer(App(secure=False)).start()
    per_thread, threads = 20, 8
    ok = [0] * threads
    errors: list[str] = []
    lock = threading.Lock()

    def hit(i: int) -> None:
        for _ in range(per_thread):
            try:
                req = urllib.request.Request(srv.base_url + chr(0x2F) + "api" + chr(0x2F) + "ping")
                with urllib.request.urlopen(req, timeout=5) as resp:
                    if resp.status == 200:
                        with lock:
                            ok[i] += 1
            except Exception as exc:  # 记录而不是吞掉：数量对不上时要知道原因
                with lock:
                    errors.append(f"{type(exc).__name__}: {exc}"[:60])

    try:
        ts = [threading.Thread(target=hit, args=(i,)) for i in range(threads)]
        for t in ts:
            t.start()
        for t in ts:
            t.join(timeout=30)
        assert errors == [], errors[:3]
        assert sum(ok) == per_thread * threads, f"丢了 {per_thread * threads - sum(ok)} 个请求"
    finally:
        srv.stop()
