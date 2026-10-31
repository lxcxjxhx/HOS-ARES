"""真实回环监听测试（B5）。

放在 ``tests/lab`` 是因为这里必须写出回环地址与端口拼装——那是**被测物**的一部分，
而 ``noroute`` 只允许靶场目录存在这类字面量。产品代码（``veripen_oracles``）里
``HttpListener`` 的 host 由调用方注入，本身不含任何回环字面量。
"""

from __future__ import annotations

import urllib.request

from veripen_oracles.oob import HttpListener, OobProbe

LOOPBACK = "127.0.0.1"


def _listener() -> HttpListener:
    return HttpListener(endpoint_value="loopback-SAMPLE", host=LOOPBACK).start()


def test_listener_records_callbacks() -> None:
    listener = _listener()
    try:
        mark = listener.snapshot()
        urllib.request.urlopen(f"http://{LOOPBACK}:{listener.port}/probe-ABC", timeout=2).read()
        verdict = OobProbe(listener).check(mark, "ABC")
        assert verdict.hit is True
        assert verdict.callbacks[0].source_host == LOOPBACK
    finally:
        listener.stop()


def test_listener_ignores_other_nonces() -> None:
    listener = _listener()
    try:
        mark = listener.snapshot()
        urllib.request.urlopen(f"http://{LOOPBACK}:{listener.port}/other", timeout=2).read()
        assert OobProbe(listener).check(mark, "ABC").hit is False
    finally:
        listener.stop()


def test_listener_marks_self_attribution() -> None:
    """自己打的回调不算"目标连了 OOB"（SSRF 误报控制第一���）。"""
    listener = _listener()
    try:
        mark = listener.snapshot()
        urllib.request.urlopen(f"http://{LOOPBACK}:{listener.port}/n-1", timeout=2).read()
        verdict = OobProbe(listener).check(mark, "n-1", self_hosts=(LOOPBACK,))
        assert verdict.hit is False
        assert verdict.reason == "callback_from_self"
    finally:
        listener.stop()
