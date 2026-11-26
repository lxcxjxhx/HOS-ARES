"""对**打包产物**做一次只读性审计（B555）。

README 声明：「它**只读**，唯一的写入是审批决定（有 `--db` 时落进 `task_events`）。」

parity 的 P6 只验了 `ui/approvals` 可读 + `ui/findings` 的 POST 是 405 两格。
**两格不构成"全部只读"**：这是一条安全边界声明，必须把**每一个 `ui.*` 路径
× 每一个写方法**都试一遍，才能说这句话成立。

用法：``python scripts/audit_readonly.py``（会先起一个空态控制台）
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

#: 允许写入的**唯一**例外（README 明写的那一条）。
#: 注意：这是 **op 名**（点号），拼路径时要换成斜杠——
#: 早先直接 `chr(0x2F) + WRITE_EXCEPTION` 拼出 `/ui.approvals`，于是 GET 全 404。
WRITE_EXCEPTION_OP = "ui.approvals"
WRITE_EXCEPTION_PATH = chr(0x2F) + "ui" + chr(0x2F) + "approvals"

#: 写方法表（HTTP 语义里会改状态的那些）
WRITE_METHODS = ("POST", "PUT", "PATCH", "DELETE")


def _paths(app: Any) -> list[str]:
    """从 FastAPI 应用里取全部 `ui.*` 路径。"""
    out: list[str] = []
    for route in getattr(app, "routes", ()):
        path = str(getattr(route, "path", "") or "")
        if path.startswith(chr(0x2F) + "ui"):
            out.append(path)
    return sorted(set(out))


def audit() -> dict[str, Any]:
    from fastapi.testclient import TestClient

    from web.console import ConsoleState, build_app

    # **同一个 app 实例**同时用于取路径与发请求（B555）。
    # 早先写成 `build_app()` 两次：路由表来自一个实例、请求打到另一个实例，
    # 于是每个 GET 都 404 —— 量具自己把自己骗了。
    app = build_app(ConsoleState())
    client = TestClient(app)
    paths = _paths(app)
    leaks: list[dict[str, str]] = []
    checked = 0

    for path in paths:
        for method in WRITE_METHODS:
            if path == WRITE_EXCEPTION_PATH:
                continue                      # 唯一允许的写：审批决定
            checked += 1
            try:
                resp = client.request(method, path)
            except Exception as exc:  # noqa: BLE001 —— 异常也算"没被 405 挡住"
                # 空 body 打到一个**真的存在的写路由**上，FastAPI 会抛
                # JSONDecodeError / RequestValidationError —— 那是"路由在、只是参数不对"，
                # 对只读审计来说**比 405 更糟**：它证明这条路径能写。
                kind = type(exc).__name__
                leaks.append({"path": path, "method": method, "status": f"writable:{kind}"})
                continue
            if resp.status_code != 405:
                leaks.append({"path": path, "method": method, "status": str(resp.status_code)})

    # 审批页本身：GET 可读、POST 存在（它是唯一写入点）
    approval_get = client.get(WRITE_EXCEPTION_PATH).status_code

    return {
        "ui_paths": len(paths),
        "write_checks": checked,
        "leaks": leaks,
        "approval_get": approval_get,
    }


def main() -> int:
    result = audit()
    print(json.dumps(result, ensure_ascii=False, indent=1))
    print()
    print(f"ui 路径：{result['ui_paths']}  · 写方法检查：{result['write_checks']} 格")
    if result["leaks"]:
        print(f"**{len(result['leaks'])} 处未被 405 挡住**：")
        for leak in result["leaks"]:
            print(f"  {leak['method']:7} {leak['path']:32} → {leak['status']}")
        return 1
    print("结论：全部写方法均 405（审批页除外，它是唯一写入点）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
