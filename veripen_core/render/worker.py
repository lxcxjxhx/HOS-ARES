"""渲染线程：把浏览器放在**没有事件循环**的线程里跑（B118）。

为什么必须这样：playwright sync API 一旦检测到当前线程有 running loop 就直接抛
``Sync API inside the asyncio loop``。控制台是 FastAPI、agent 也可能在 loop 里，
所以渲染不能跟着调用方线程走。

对外只有三个动作：``capture(...)``（同步阻塞，内部投递到工作线程）、
``close()``、上下文管理器。浏览器整个生命周期都在工作线程里，不跨线程使用。
"""

from __future__ import annotations

import queue
import threading
from dataclasses import dataclass, field
from typing import Any

from .browser import BrowserRenderer, CaptureResult

_STOP = object()


@dataclass
class _Task:
    kwargs: dict[str, Any]
    done: threading.Event = field(default_factory=threading.Event)
    result: CaptureResult | None = None
    error: BaseException | None = None


class RenderThread:
    """一个专属线程 + 一个浏览器，串行执行渲染请求。"""

    def __init__(self, *, browser: Any = None) -> None:
        self._queue: queue.Queue[Any] = queue.Queue()
        self._browser = browser
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, name="veripen-render", daemon=True)
        self._thread.start()
        self._ready.wait(timeout=30)
        if self._browser is None:
            raise RuntimeError("渲染线程启动失败：浏览器没起来")

    # ── 工作线程主体 ────────────────────────────────────────────
    def _run(self) -> None:
        try:
            if self._browser is None:
                self._browser = BrowserRenderer()
                self._browser.__enter__()
        except BaseException:  # noqa: BLE001 - 起不来就是起不来，交给调用方
            self._browser = None
            self._ready.set()
            return
        self._ready.set()
        while True:
            task = self._queue.get()
            if task is _STOP:
                break
            try:
                task.result = self._browser.capture(**task.kwargs)
            except BaseException as exc:  # noqa: BLE001
                task.error = exc
            finally:
                task.done.set()
        try:
            self._browser.__exit__(None, None, None)
        except Exception:  # noqa: BLE001 - 关不掉不该盖住主流程
            pass
        self._browser = None

    # ── 对外 ──────────────────────────────────────────────────
    def capture(self, **kwargs: Any) -> CaptureResult:
        task = _Task(kwargs=kwargs)
        self._queue.put(task)
        task.done.wait()
        if task.error is not None:
            raise task.error
        assert task.result is not None
        return task.result

    def close(self) -> None:
        if self._thread.is_alive():
            self._queue.put(_STOP)
            self._thread.join(timeout=30)

    def __enter__(self) -> RenderThread:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


__all__ = ["RenderThread"]
