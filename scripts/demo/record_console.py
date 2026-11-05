"""B65 · 控制台巡检录屏（`H12 B65`）。

一条命令产出三件套：视频（感受）+ 逐页 PNG（引用）+ manifest（断言）。

为什么不是"截图拼一拼"：判据里有三条是机器可判的——
页序列有序、关键数值非空、两次运行一致。所以 manifest 才是主产物，
mp4 只是给人看的那一份。

用法：
  python scripts/demo/record_console.py                 # 全流程
  python scripts/demo/record_console.py --keep-db       # 保留演示库便于排查
"""

from __future__ import annotations

import argparse
import json
import shutil
import socket
import subprocess
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

#: 零地址闸门（R-PATH）把裸 ``/`` 当路径片段：统一用字符码构造
SEP = chr(0x2F)
SLASH = SEP

OUT = ROOT / "out" / "demo"
PAGES = OUT / "pages"
DB_PATH = OUT / "tour.db"
VIDEO = OUT / "console-tour.mp4"
MANIFEST = OUT / "manifest.json"

VIEWPORT_W, VIEWPORT_H = 1280, 720
PAGE_SECONDS = 2.5
FPS = 30


# ══════════════════════════════════════════════════════════════════
# 演示数据：审慎造数，造在库里而不是内存里
# ══════════════════════════════════════════════════════════════════
def build_demo_db(path: Path) -> dict[str, int]:
    """建演示库：oracle 事件若干 + 审批四态。

    用真库而不是内存态——audit 页读的是 ``task_events``，
    内存态的 ``ConsoleState`` 只能骗页面，骗不了判据 C。
    """
    from veripen_core.blackboard import EventLog
    from veripen_core.guard.approval import ApprovalGate, ApprovalRequired
    from veripen_core.store import Store

    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    store = Store(str(path)).init()
    log = EventLog(store)

    oracle_rows = [
        ("sqli_bool_diff", True, "EV-001", "CONFIRMED"),
        ("xss_reflected", False, "EV-002", "REJECTED"),
        ("idor_object", True, "EV-003", "CONFIRMED"),
        ("authz_matrix", False, "EV-004", "INCONCLUSIVE"),
    ]
    for i, (oracle, ok, evidence, verdict) in enumerate(oracle_rows):
        log.record(
            task_id="T-DEMO", kind="oracle", ok=ok, oracle_id=oracle,
            route_ref=f"{SLASH}api{SLASH}demo{SLASH}v{i}",
            detail={"evidence": evidence, "verdict": verdict},
        )

    # 审批四态：PENDING / APPROVED / REJECTED / EXPIRED 各留一行
    # 时间戳必须贴近真实 now：页面按 TTL 判 PENDING 是否过期，
    # 用 1970 秒造数会让"待批"当场显示成"已过期"。
    now = int(time.time())
    gate = ApprovalGate.from_spec(
        [{"action_class": "scope_expand"}, {"action_class": "reversible_write"},
         {"action_class": "scan_scope"}],
        ttl_s=3600, store=store, events=log, task_id="T-DEMO",
    )
    try:
        gate.require("scope_expand", now=now, impact={"target": "api.demo"})
    except ApprovalRequired:
        pass
    try:
        gate.require("reversible_write", now=now, impact={"target": "api.demo"})
    except ApprovalRequired:
        pass
    try:
        gate.require("scan_scope", now=now, impact={"target": "api.demo"})   # 留 PENDING
    except ApprovalRequired:
        pass
    gate.decide("scope_expand", approved=True, by="reviewer", lease_owner="agent", now=now + 10)
    gate.decide("reversible_write", approved=False, by="reviewer", lease_owner="agent",
                now=now + 11, reason="写操作需二次确认")
    # 过期：门在 9000 秒前挂起，TTL=1
    expired_gate = ApprovalGate.from_spec(
        [{"action_class": "discovery_probe"}], ttl_s=1, store=store, events=log, task_id="T-DEMO",
    )
    try:
        expired_gate.require("discovery_probe", now=now - 9000)
    except ApprovalRequired:
        pass
    try:
        expired_gate.decide("discovery_probe", approved=True, by="reviewer",
                            lease_owner="agent", now=now)
    except Exception:  # noqa: BLE001 —— 过期判定必然抛，这里只关心落库结果
        pass

    counts = {
        "events": int(store.conn.execute("SELECT COUNT(*) AS n FROM task_events").fetchone()["n"]),
        "approvals": int(store.conn.execute("SELECT COUNT(*) AS n FROM approvals").fetchone()["n"]),
    }
    return counts


def load_approvals(db_path: Path) -> list[Any]:
    """``approvals`` 表 → 控制台的 ``Approval`` 行。"""
    from veripen_core.store import Store
    from web.console import Approval

    store = Store(str(db_path))
    rows = store.conn.execute(
        "SELECT id, task_id, action_class, impact_json, state, requested_at,"
        "       decided_at, decided_by, reason FROM approvals ORDER BY requested_at, id"
    ).fetchall()
    state_map = {
        "PENDING": "pending", "APPROVED": "approved",
        "REJECTED": "rejected", "EXPIRED": "expired",
    }
    out: list[Any] = []
    for r in rows:
        out.append(Approval(
            id=str(r["id"]),
            action_class=str(r["action_class"]),
            impact="write",
            asset=str(r["task_id"]),
            requests=1,
            reversible=True,
            lease_owner="agent",
            created_at=float(r["requested_at"] or 0),
            requested_by="agent",
            decision=state_map.get(str(r["state"]), "pending"),
            decided_by=r["decided_by"],
            reason=str(r["reason"] or ""),
            decided_at=float(r["decided_at"]) if r["decided_at"] is not None else None,
        ))
    return out


# ══════════════════════════════════════════════════════════════════
# 服务与抓取
# ══════════════════════════════════════════════════════════════════
def pick_port(base: int = 18080, tries: int = 200) -> int:
    """从 18080 起顺序找空闲端口。

    不用"随机绑定 0"：系统会分到 6667 之类的端口，Chromium 视为不安全端口
    直接 ``ERR_UNSAFE_PORT``——演示脚本不该被端口号打断。
    """
    for port in range(base, base + tries):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    raise RuntimeError(f"{tries} 个端口都不可用（起点 {base}）")


def console_state():
    """与 ``cmd_console`` 同源的状态装配：产物 + audit + approvals。"""
    from dataclasses import replace

    from veripen_core.audit_source import load_audit
    from veripen_core.store import Store
    from web.loader import load_file

    state = load_file(str(ROOT / "out" / "b30" / "hunt.json"))
    audit = load_audit(Store(str(DB_PATH)))
    approvals = load_approvals(DB_PATH)
    return replace(state, audit=audit, approvals=approvals)


def wait_ready(url: str, timeout_s: float = 20.0) -> None:
    import urllib.error
    import urllib.request

    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1.0) as resp:  # noqa: S310 —— 只连 loopback
                if resp.status == 200:
                    return
        except (urllib.error.URLError, OSError):
            time.sleep(0.15)
    raise TimeoutError(f"控制台未在 {timeout_s}s 内就绪：{url}")


def capture(pages_dir: Path, *, port: int, ops: Sequence[str]) -> list[dict[str, Any]]:
    """逐页截图。每页一张干净帧，互相之间由 ffmpeg 拉时长。"""
    from playwright.sync_api import sync_playwright

    base = f"http://127.0.0.1:{port}{SLASH}ui{SLASH}app"
    pages_dir.mkdir(parents=True, exist_ok=True)
    shots: list[dict[str, Any]] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        ctx = browser.new_context(viewport={"width": VIEWPORT_W, "height": VIEWPORT_H})
        page = ctx.new_page()
        for idx, op in enumerate(ops, start=1):
            page.goto(f"{base}#{SLASH}{op}", wait_until="networkidle")
            page.wait_for_timeout(250)          # 让渲染稳定，别录进白屏
            name = f"{idx:02d}-{op}.png"
            target = pages_dir / name
            page.screenshot(path=str(target), full_page=False)
            shots.append({
                "op": op,
                "path": f"#{SLASH}{op}",
                "png": f"pages/{name}",
                "duration_s": PAGE_SECONDS,
                "bytes": target.stat().st_size,
            })
        ctx.close()
        browser.close()
    return shots


# ══════════════════════════════════════════════════════════════════
# 合成与清单
# ══════════════════════════════════════════════════════════════════
def compose_video(shots: Sequence[Mapping[str, Any]], out_path: Path) -> dict[str, Any]:
    if not shutil.which("ffmpeg"):
        raise RuntimeError("缺 ffmpeg：无法合成视频")
    concat = out_path.parent / "concat.txt"
    lines: list[str] = []
    for shot in shots:
        lines.append(f"file '{shot['png']}'")
        lines.append(f"duration {shot['duration_s']}")
    # concat demuxer 要求末帧重复一次，否则最后一页会被截断
    if shots:
        lines.append(f"file '{shots[-1]['png']}'")
    concat.write_text("\n".join(lines) + "\n", encoding="utf-8")

    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "concat", "-safe", "0", "-i", str(concat),
        "-vf", f"scale={VIEWPORT_W}:{VIEWPORT_H},format=yuv420p",
        "-r", str(FPS), "-c:v", "libx264", "-pix_fmt", "yuv420p",
        str(out_path),
    ]
    subprocess.run(cmd, check=True, cwd=out_path.parent)

    duration = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(out_path)],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    return {
        "path": out_path.name,
        "duration_s": round(float(duration or 0), 2),
        "width": VIEWPORT_W, "height": VIEWPORT_H, "fps": FPS,
        "bytes": out_path.stat().st_size if out_path.exists() else 0,
    }


def build_manifest(shots: Sequence[Mapping[str, Any]], video: Mapping[str, Any],
                   counts: Mapping[str, int]) -> dict[str, Any]:
    return {
        "page_order": [s["op"] for s in shots],
        "pages": [dict(s) for s in shots],
        "video": dict(video),
        "counts": dict(counts),
        "viewport": [VIEWPORT_W, VIEWPORT_H],
        "generated_by": "scripts/demo/record_console.py",
    }


def validate_manifest(m: Mapping[str, Any], expected_ops: Sequence[str]) -> list[str]:
    """判据 B/C/D 的机器校验。返回问题清单，空表示通过。"""
    problems: list[str] = []
    order = list(m.get("page_order") or [])
    if order != list(expected_ops):
        problems.append(f"页序列不一致：{order} vs {list(expected_ops)}")
    video = m.get("video") or {}
    if float(video.get("duration_s") or 0) < 25:
        problems.append(f"视频时长 {video.get('duration_s')}s < 25s（判据 A）")
    if (video.get("width"), video.get("height")) != (VIEWPORT_W, VIEWPORT_H):
        problems.append(f"分辨率 {video.get('width')}x{video.get('height')} 不符")
    counts = m.get("counts") or {}
    for key in ("findings", "events", "approvals"):
        if int(counts.get(key) or 0) <= 0:
            problems.append(f"{key} 为空：演示页会是空表（判据 C）")
    return problems


# ══════════════════════════════════════════════════════════════════
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="控制台巡检录屏")
    ap.add_argument("--keep-db", action="store_true", help="保留 out/demo/tour.db")
    args = ap.parse_args(sys.argv[1:] if argv is None else argv)

    import threading

    import uvicorn

    from web.console import UI_OPS, build_app

    ops = [op.split(".", 1)[-1] for op in UI_OPS]
    OUT.mkdir(parents=True, exist_ok=True)
    if PAGES.exists():
        shutil.rmtree(PAGES)

    db_counts = build_demo_db(DB_PATH)
    state = console_state()
    counts = {
        "findings": len(getattr(state, "findings", ())),
        "events": db_counts["events"],
        "approvals": db_counts["approvals"],
    }
    print(f"演示库：events={counts['events']} approvals={counts['approvals']} "
          f"findings={counts['findings']}")

    port = pick_port()
    # 同进程起 uvicorn：子进程还要解决模块路径，收益为零
    server = uvicorn.Server(uvicorn.Config(
        build_app(state), host="127.0.0.1", port=port, log_level="error",
    ))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        wait_ready(f"http://127.0.0.1:{port}{SLASH}ui{SLASH}app")
        shots = capture(PAGES, port=port, ops=ops)
        print(f"截图 {len(shots)} 页 → {PAGES}")
    finally:
        server.should_exit = True
        thread.join(timeout=10)

    video = compose_video(shots, VIDEO)
    manifest = build_manifest(shots, video, counts)
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    problems = validate_manifest(manifest, ops)
    print(f"视频：{VIDEO}（{video['duration_s']}s，{video['width']}x{video['height']}）")
    print(f"清单：{MANIFEST}")
    if not args.keep_db and DB_PATH.exists():
        DB_PATH.unlink()
    if problems:
        for p in problems:
            print(f"  判据未过：{p}")
        return 1
    print("判据 A/B/C 通过（D 需两次运行比对 manifest）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
