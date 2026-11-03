"""成本测量：bench 若走真网络，44 例各起一个服务要多久？

这批不换 bench，只**量代价**——因为换法取决于测量结果：

- 若「每例起一个 LabServer」的总耗时可控（< 30s），直接切，最简单、无状态残留；
- 若不可控，就得「共享服务 + 每例重置 App」，但那要确认 ``App`` 的哪些字段是状态。

所以先测三件事：
1. 起停一个服务的耗时；
2. 44 次起停的总耗时；
3. ``App`` 的状态字段是否可以被"换一个新实例"重置（而不是靠清空）。

**为什么不用"共享服务 + reset()"作为默认**：``App`` 有 ``callbacks`` / ``log`` / ``state``
三处可变字段，reset 漏一个就会让上一例的副作用渗到下一例——那是最难查的一类假绿。
「每例新实例」没有这个风险，代价只是时间；时间可测，渗漏不可测。
"""

from __future__ import annotations

import time
from dataclasses import asdict
from typing import Any

from lab.app import App
from lab.net_server import LabServer


def measure_start_stop(repeats: int = 5) -> dict[str, Any]:
    srv = LabServer(App(secure=False)).start()
    srv.stop()

    start = time.perf_counter()
    for _ in range(repeats):
        s = LabServer(App(secure=False)).start()
        s.stop()
    elapsed = time.perf_counter() - start
    return {"repeats": repeats, "total_s": round(elapsed, 3),
            "per_cycle_ms": round(elapsed / repeats * 1000, 1)}


def measure_44_cycles(cases: int = 44) -> dict[str, Any]:
    """模拟 bench 的规模：44 次起停（每例一个实例）。"""
    start = time.perf_counter()
    for _ in range(cases):
        s = LabServer(App(secure=False)).start()
        s.stop()
    elapsed = time.perf_counter() - start
    return {
        "cases": cases,
        "total_s": round(elapsed, 2),
        "per_case_ms": round(elapsed / cases * 1000, 1),
        "verdict": "可接受" if elapsed < 30 else "太重，需共享服务",
    }


def mutable_fields() -> list[str]:
    """``App`` 里会随请求变化的字段。**共享服务必须先能重置这些。**"""
    a = App()
    b = App()
    names: list[str] = []
    for key, val in asdict(a).items():
        if isinstance(val, (list, dict)) and val != asdict(b)[key]:
            names.append(key)
        elif isinstance(val, (list, dict)):
            names.append(key)
    return sorted(names)


def reset_isolation_check() -> dict[str, Any]:
    """两例之间换新实例 → 状态是否真的隔离。"""
    a = App(secure=False)
    a.callbacks.append({"nonce": "leak"})
    a.log.append({"x": 1})
    a.state["rate_hits"] = 99

    b = App(secure=False)
    return {
        "callbacks_clean": b.callbacks == [],
        "log_clean": b.log == [],
        "rate_clean": b.state.get("rate_hits") == 0,
        "isolated": b.callbacks == [] and b.log == [] and b.state.get("rate_hits") == 0,
    }


def main() -> int:
    print("== 起停耗时 ==")
    print(measure_start_stop())
    print("== 44 例规模 ==")
    print(measure_44_cycles())
    print("== App 可变字段 ==")
    print(mutable_fields())
    print("== 新实例隔离 ==")
    print(reset_isolation_check())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
