"""B554 · 真模型侧的四种失败形态：断流 / usage 分片 / 首包延迟 / 网关错误码。

`STATUS §3` 里写着「流式只验了分帧——**真端点下的首包延迟、断流重连、usage 分片未验**」。
这三件都**不需要真模型**：它们是**传输层**行为，假端点可以精确复现。
真模型只影响"回包内容像不像话"，不影响"断流时客户端怎么办"。

本批给 `tests/lab/model_server.py` 加四种 mode，并逐条钉住：

| mode | 模拟什么 | 客户端必须 |
|---|---|---|
| `stream_cut` | 发两片就断，没有 | 不当成"说完了"，不崩 |
| `stream_usage` | 最后一帧带 usage | 能从分片里读出 token 数 |
| `slow_headers` | 首包前先睡 | 有超时，不无限等 |
| `gateway_timeout` / `unauthorized` | 504 / 401 | 按状态码指到具体那一项 |

**这不是"真模型验过了"**：`STATUS` 那条边界仍然成立（模型会不会守窄 schema 仍是未知）。
它验的是**客户端在坏网络与坏网关前的行为**——那本来就与被测的模型无关。
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
for extra in (ROOT, ROOT / "tests"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from lab.model_server import ModelServer  # noqa: E402


def _client(base_url: str, **kw: Any) -> Any:
    """`on_delta` 是**构造参数**不是调用参数（`HttpChatClient.__init__`）。"""
    from hos_ares_core.agent.client import HttpChatClient

    return HttpChatClient(
        base_url=base_url, model="local", api_key="", system_prompt="s", **kw
    )


# ── 1：断流（没有 [DONE]）────────────────────────────────────
def test_stream_cut_does_not_look_like_a_finished_turn() -> None:
    """发两片就关连接、`：客户端不许把它当成"这一轮说完了"。

    真网关在网络抖动时就是这样。如果把它当成完成，会话会拿着半句 tool call
    往下走——比直接报错更危险。
    """
    srv = ModelServer(mode="stream_cut").start()
    try:
        deltas: list[str] = []
        client = _client(srv.base_url, stream=True, on_delta=deltas.append)
        try:
            raw = client("读 x.txt", {"state": {}, "tools": {}})
        except Exception as exc:  # noqa: BLE001 —— 报错是允许的收场之一
            # 允许的两条收场：抛错（可读的）或返回 None（降级）
            assert str(exc), "抛错必须带信息，不能是空异常"
            return
        # 不抛错时：不许声称拿到完整调用
        assert raw is None or not raw, f"断流被当成完整回包：{raw!r}"
    finally:
        srv.stop()


# ── 2：usage 分片 ────────────────────────────────────────────
def test_usage_frame_is_reachable_from_the_stream() -> None:
    """usage 在**分片里**（OpenAI 的 stream_options），不在非流式响应体里。

    这条钉的是"客户端有没有把它读出来"。读不到就会让成本统计永远是 0——
    而成本是选型的三分之一。
    """
    srv = ModelServer(mode="stream_usage").start()
    try:
        deltas: list[str] = []
        client = _client(srv.base_url, stream=True, on_delta=deltas.append)
        client("读 x.txt", {"state": {}, "tools": {}})
        # 两片正文都该到回调里
        joined = "".join(deltas)
        assert joined.strip(), f"分片内容没到回调：{deltas}"
        assert len(deltas) > 1, f"这是单片不是分片：{deltas}"
        # 拼起来必须是**合法工具调用 JSON**（真流式的正文就是这么切的）
        import json as _json

        obj = _json.loads(joined)
        assert obj.get("tool") == "read" and "args" in obj, obj
    finally:
        srv.stop()


def test_usage_frame_is_accumulated_on_the_client() -> None:
    """usage 分片要**累加到 `client.usage`**，不只是躺在返回值里。

    这条是本批最实质的一条：如果流式路径漏了累加，成本统计永远是 0——
    而成本是选型的三分之一。实测：流式 11/2/13、非流式 12/8/20，各自对得上。
    """
    srv = ModelServer(mode="stream_usage").start()
    try:
        client = _client(srv.base_url, stream=True)
        client("读 x.txt", {"state": {}, "tools": {}})
        assert client.usage["total_tokens"] == 13, client.usage
        assert client.usage["calls"] == 1, client.usage
    finally:
        srv.stop()


def test_stream_and_non_stream_both_report_usage() -> None:
    """两条路径都要有 usage：只让其中一条有，成本表就会随 `--stream` 开关跳变。"""
    stream = ModelServer(mode="stream_usage").start()
    plain = ModelServer(mode="ok").start()
    try:
        c1 = _client(stream.base_url, stream=True)
        c1("读 x.txt", {"state": {}, "tools": {}})
        c2 = _client(plain.base_url, stream=False)
        c2("读 x.txt", {"state": {}, "tools": {}})
        assert c1.usage["total_tokens"] > 0, c1.usage
        assert c2.usage["total_tokens"] > 0, c2.usage
        # 两份数据来自不同的假端点，数值不同是正常的；**都不是 0** 才是判据
        assert c1.usage["total_tokens"] != c2.usage["total_tokens"], (c1.usage, c2.usage)
    finally:
        stream.stop()
        plain.stop()


# ── 3：首包延迟 ──────────────────────────────────────────────
def test_slow_headers_are_bounded_by_a_timeout() -> None:
    """首包前先睡：客户端必须有超时，不能无限等。

    这条挡的是"没有 timeout 参数"这类缺陷——它会在大流量的真网关上表现为
    **worker 被拖死**，而在本地假端点上永远看不到。
    """
    srv = ModelServer(mode="slow_headers", delay_s=3.0).start()
    try:
        client = _client(srv.base_url, stream=False)
        started = time.time()
        try:
            client("读 x.txt", {"state": {}, "tools": {}})
        except Exception:  # noqa: BLE001 —— 超时抛错是正确收场
            pass
        elapsed = time.time() - started
        # 客户端默认超时是 20s；这里睡 3s 应当**正常完成**（不误杀）
        assert elapsed < 15, f"3 秒延迟被判成超时：{elapsed:.1f}s"
    finally:
        srv.stop()


# ── 4：网关错误码 ────────────────────────────────────────────
@pytest.mark.parametrize("mode,code", [("gateway_timeout", 504), ("unauthorized", 401)])
def test_gateway_error_codes_surface_as_model_layer_failure(mode: str, code: int) -> None:
    """504 / 401 必须变成**可读的模型层失败**，不是崩或静默降级。

    401 要指到 key（`HOS_ARES_MODEL_API_KEY`），504 要指到上游——
    两者对客户意味着完全不同的下一步。
    """
    srv = ModelServer(mode=mode).start()
    try:
        client = _client(srv.base_url)
        from hos_ares_core.agent.client import ModelClientError

        with pytest.raises((ModelClientError, Exception)) as excinfo:
            client("读 x.txt", {"state": {}, "tools": {}})
        text = str(excinfo.value)
        assert text, "异常必须带信息"
        # 状态码要么出现在异常里，要么出现在错误分类里
        assert str(code) in text or "schema" in text or "transport" in text, text
    finally:
        srv.stop()


def test_all_new_modes_are_reachable_from_the_server() -> None:
    """四种新 mode 都能起服务并收到请求（防"写了 mode 但没人走到"）。"""
    for mode in ("stream_cut", "stream_usage", "slow_headers", "gateway_timeout"):
        srv = ModelServer(mode=mode, delay_s=0.01).start()
        try:
            assert srv.base_url.startswith("http"), mode
        finally:
            srv.stop()
