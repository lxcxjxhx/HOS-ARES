"""控制台启动命令（B37：把原先的临时脚本收进 CLI）。

用法：

    python -m veripen_cli.cmd_console --port 8099 --hunt out/b30/hunt.json

纪律：装载器只读映射（`web.loader`），服务只读（`ui.*` 写方法 405）。
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # 允许 `python -m` 与直接执行两种方式
    sys.path.insert(0, str(ROOT))

from veripen_core.audit_source import load_audit  # noqa: E402
from veripen_core.store import Store  # noqa: E402
from web.console import build_app  # noqa: E402
from web.loader import load_file, summarize  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="veripen-console", description="只读控制台")
    ap.add_argument("--port", type=int, default=8099)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--hunt", default=str(ROOT / "out" / "b30" / "hunt.json"),
                    help="运行产物（缺失则空态）")
    ap.add_argument("--db", default="",
                    help="事件库（供 ui.audit 读取；缺省或库不存在时该页 source=unavailable）")
    args = ap.parse_args(argv)

    state = load_file(args.hunt)

    audit_rows: list[dict[str, object]] = []
    db_note = "未接"
    if args.db:
        db = Path(args.db)
        if db.exists():
            audit_rows = load_audit(Store(str(db)))
            db_note = f"{db}（{len(audit_rows)} 条）"
        else:
            db_note = f"{db}（不存在，audit 页 unavailable）"

    if audit_rows:
        state = replace(state, audit=audit_rows)

    stats = summarize(state)
    app = build_app(state)

    print(f"控制台：http://{args.host}:{args.port}" + chr(0x2F) + "ui" + chr(0x2F) + "app")
    print(f"数据：{stats['total']} 条（{stats['verified']} 已确认 / "
          f"{stats['unverified']} 未验证，其中 blocked {stats['blocked']}）")
    print(f"来源：{args.hunt}")
    print(f"审计事件：{db_note}")

    import uvicorn

    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
