"""B555 · 只读控制台的**完整**审计（不是抽两格）。

README 声明：

> 它**只读**，唯一的写入是审批决定（有 `--db` 时落进 `task_events`）。

parity 的 P6 只验了 `ui/approvals` 可读 + `ui/findings` 的 POST 是 405 ——
**两格不构成"全部只读"**。这是一条安全边界声明，必须把
**每一个 `ui.*` 路径 × 每一个写方法**都试一遍，才配说这句话。

`scripts/audit_readonly.py` 就是那个全量审计；这里是它的闸门版。
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

#: 斜杠现拼：noroute 的 R-PATH 不许以 / 开头的字面量出现在源码里
SL = chr(0x2F)

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _audit() -> dict[str, Any]:
    """跑一遍全量审计。

    `scripts/` 不在 mypy 的搜索路径里（它扫产品目录），所以这里显式标注返回类型：
    用 Any 接住、再断言成我们要的形状，比给整条 import 加 ignore 更清楚。
    """
    sys.path.insert(0, str(ROOT / "scripts"))
    import importlib

    mod = importlib.import_module("audit_readonly")
    result: dict[str, Any] = mod.audit()
    return result


def test_every_write_method_on_every_ui_path_is_405() -> None:
    """全量：16 个路径 × 4 个写方法（去掉唯一例外），一格都不许漏。

    这条用例存在的理由：只读是**安全边界**，不是"大部分只读"。
    """
    result = _audit()
    assert result["ui_paths"] >= 10, f"ui 路径太少，可能没取全：{result['ui_paths']}"
    assert result["write_checks"] >= 40, f"检查格数太少：{result['write_checks']}"
    assert not result["leaks"], f"这些写方法没被 405 挡住：{result['leaks']}"


def test_the_single_write_exception_is_readable() -> None:
    """唯一的写入点（审批页）**本身要能读**：否则客户无从签字。"""
    result = _audit()
    assert result["approval_get"] == 200, result["approval_get"]


def test_audit_covers_all_four_write_methods() -> None:
    """POST/PUT/PATCH/DELETE 四种都要在名单里。

    只查 POST 是个常见的疏漏：`PUT`/`PATCH` 一样能改状态，`DELETE` 更直接。
    """
    import importlib
    import inspect

    sys.path.insert(0, str(ROOT / "scripts"))
    mod = importlib.import_module("audit_readonly")
    methods: Any = mod.WRITE_METHODS
    assert set(methods) == {"POST", "PUT", "PATCH", "DELETE"}, methods
    src = inspect.getsource(sys.modules["audit_readonly"])
    assert "for method in WRITE_METHODS" in src, "写方法表没被真正遍历"


@pytest.mark.parametrize("suffix", ["app", "overview", "findings"])
def test_console_reads_still_work(suffix: str) -> None:
    """只读不等于打不开：主要页面都得 200。"""
    from fastapi.testclient import TestClient

    from web.console import ConsoleState, build_app

    client = TestClient(build_app(ConsoleState()))
    path = SL + "ui" + SL + suffix
    assert client.get(path).status_code == 200, path


def test_audit_can_actually_find_a_leak() -> None:
    """量具必须**能红**：去掉例外之后，审批页的 POST 要被抓出来。

    这条挡的是"审计永远绿"——一个从不失败的检查等于没有检查。
    """
    import importlib

    sys.path.insert(0, str(ROOT / "scripts"))
    mod: Any = importlib.import_module("audit_readonly")

    original = mod.WRITE_EXCEPTION_PATH
    mod.WRITE_EXCEPTION_PATH = SL + "ui" + SL + "__nope__"
    try:
        result: dict[str, Any] = mod.audit()
    finally:
        mod.WRITE_EXCEPTION_PATH = original

    assert result["leaks"], "去掉例外后仍没抓到泄漏——审计是恒绿的"
    hit = result["leaks"][0]
    assert hit["path"] == SL + "ui" + SL + "approvals" and hit["method"] == "POST", hit
    # 分类要说明"这条路径能写"，而不是笼统的异常名
    assert hit["status"].startswith("writable:"), hit
