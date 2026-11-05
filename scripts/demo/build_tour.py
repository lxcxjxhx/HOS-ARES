"""B70 · 成片与一键演示（`H15 B70`）。

把两段素材（B65 控制台 10 页、B69 三幕终端）与章节卡合成一支成片。

**不重编码素材的假设是错的**：卡片 PNG 与 mp4 流参数不一致时直接进 concat
会在接缝处花屏。所以卡片先各自转成同参数（1280×720/30fps/yuv420p）的 mp4，
再与素材一起进 concat —— 接缝处不出花。
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[2]
SEP = chr(0x2F)
OUT = ROOT / "out" / "demo"
CARDS = OUT / "cards"
CLIPS = OUT / "clips"
TOUR = OUT / "hos-ares-tour.mp4"
MANIFEST = OUT / "tour-manifest.json"

W, H, FPS = 1280, 720, 30

BG = "#101319"
FG = "#e6e9ef"
DIM = "#8a91a0"
ACCENT = "#7ee787"
RULE = "#2a3140"

#: 章节定义：卡片 → 素材 → 卡片 …
SEGMENTS: list[dict[str, Any]] = [
    {"kind": "card", "id": "intro", "duration": 2.5,
     "title": "HOS-ARES", "sub": "三支柱 · 一台能干活的机器",
     "bullets": ["判定要硬（VeriPen）", "执行要能打（strix）",
                 "工作台要能写能跑（codex）", "平台要能指挥能复盘（驭智）"]},
    {"kind": "card", "id": "ch-console", "duration": 1.5,
     "title": "01 · 控制台", "sub": "10 个只读页",
     "bullets": ["未验证不冒充高危", "审批是唯一写入点", "审计页接事件表"]},
    {"kind": "video", "id": "console", "path": OUT / "console-tour.mp4"},
    {"kind": "card", "id": "ch-terminal", "duration": 1.5,
     "title": "02 · 三幕终端", "sub": "run / agent / platform",
     "bullets": ["run：给目标，跑完给交付物", "agent：一条指令一次动作，可恢复",
                 "platform：多资产 + 审批阻断 + 审计节"]},
    {"kind": "video", "id": "terminal", "path": OUT / "terminal" / "terminal-tour.mp4"},
    {"kind": "card", "id": "outro", "duration": 2.5,
     "title": "复现", "sub": "一条命令",
     "bullets": ["make demo", "证据：out/demo/（视频 · 帧 · manifest）",
                 "账本：.veripen/batches.jsonl"]},
]


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _card_html(seg: dict[str, Any]) -> str:
    bullets = "".join(
        f'<li style="color:{FG}">{_esc(b)}</li>' for b in seg.get("bullets") or []
    )
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
* {{ box-sizing:border-box; margin:0; }}
body {{ background:{BG}; color:{FG}; font-family:"JetBrains Mono",Consolas,monospace; }}
.wrap {{ height:{H}px; display:flex; flex-direction:column; justify-content:center;
         padding:0 96px; }}
.eyebrow {{ color:{DIM}; font-size:14px; letter-spacing:.22em; text-transform:uppercase;
            margin-bottom:18px; }}
h1 {{ font-size:56px; font-weight:600; letter-spacing:-.01em; }}
.sub {{ color:{ACCENT}; font-size:20px; margin-top:14px; }}
hr {{ border:0; border-top:1px solid {RULE}; margin:30px 0 26px; }}
ul {{ list-style:none; display:grid; gap:12px; }}
li {{ font-size:20px; line-height:1.5; padding-left:22px; position:relative; }}
li::before {{ content:"›"; position:absolute; left:0; color:{ACCENT}; }}
</style></head><body><div class="wrap">
  <div class="eyebrow">HOS-ARES</div>
  <h1>{_esc(seg.get("title") or "")}</h1>
  <div class="sub">{_esc(seg.get("sub") or "")}</div>
  <hr>
  <ul>{bullets}</ul>
</div></body></html>"""


def _render_cards(cards_dir: Path) -> dict[str, Path]:
    from playwright.sync_api import sync_playwright

    cards_dir.mkdir(parents=True, exist_ok=True)
    out: dict[str, Path] = {}
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_context(viewport={"width": W, "height": H}).new_page()
        for seg in SEGMENTS:
            if seg["kind"] != "card":
                continue
            page.set_content(_card_html(seg), wait_until="load")
            page.wait_for_timeout(60)
            target = cards_dir / f"{seg['id']}.png"
            page.screenshot(path=str(target))
            out[seg["id"]] = target
        browser.close()
    return out


def _png_to_clip(png: Path, duration: float, target: Path) -> Path:
    """单图 → 固定时长片段。

    ``-loop 1`` 必须配 ``-framerate``，且 ``-t`` 要放在 ``-i`` **之后**（输出时长）；
    放在输入前会被解释成"读输入多久"，单图输入直接报错退出。
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error",
        "-loop", "1", "-framerate", str(FPS), "-i", str(png),
        "-t", f"{duration}",
        "-vf", f"scale={W}:{H},format=yuv420p", "-r", str(FPS),
        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(target),
    ], check=True)
    return target


def _probe_duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    return float(out or 0)


def _compose(clips: Sequence[Path], target: Path) -> Path:
    listing = OUT / "tour-concat.txt"
    lines: list[str] = []
    for clip in clips:
        lines.append(f"file '{clip.as_posix()}'")
    listing.write_text("\n".join(lines) + "\n", encoding="utf-8")
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "concat", "-safe", "0", "-i", str(listing),
        "-vf", f"scale={W}:{H},format=yuv420p", "-r", str(FPS),
        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(target),
    ], check=True)
    return target


def validate_manifest(m: dict[str, Any]) -> list[str]:
    """判据 A/B/C 的机器校验。返回问题清单，空表示通过。"""
    problems: list[str] = []
    video = m.get("video") or {}
    duration = float(video.get("duration_s") or 0)
    if duration < 40:
        problems.append(f"成片 {duration}s < 40s（判据 A）")
    if (video.get("width"), video.get("height")) != (W, H):
        problems.append(f"分辨率 {video.get('width')}x{video.get('height')} 不符")
    ids = [s.get("id") for s in m.get("segments") or []]
    for need in ("intro", "ch-console", "console", "ch-terminal", "terminal", "outro"):
        if need not in ids:
            problems.append(f"缺少段落：{need}（判据 B）")
    expected = float(m.get("expected_duration_s") or 0)
    if expected and abs(duration - expected) > 1.5:
        problems.append(
            f"成片 {duration:.2f}s 与预期 {expected:.2f}s 相差 >1.5s（判据 C）"
        )
    return problems


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="成片合成")
    ap.add_argument("--skip-check", action="store_true", help="素材缺失时只报警不退出")
    args = ap.parse_args(sys.argv[1:] if argv is None else argv)

    if not shutil.which("ffmpeg"):
        raise RuntimeError("缺 ffmpeg")

    missing = [str(s["path"]) for s in SEGMENTS
               if s["kind"] == "video" and not Path(s["path"]).exists()]
    if missing:
        print("缺素材：")
        for m in missing:
            print("  " + m)
        print("先跑：python scripts/demo/record_console.py && python scripts/demo/record_terminal.py")
        if not args.skip_check:
            return 2

    OUT.mkdir(parents=True, exist_ok=True)
    for d in (CARDS, CLIPS):
        if d.exists():
            shutil.rmtree(d)
    cards = _render_cards(CARDS)

    clips: list[Path] = []
    segments: list[dict[str, Any]] = []
    expected = 0.0
    for seg in SEGMENTS:
        if seg["kind"] == "card":
            clip = _png_to_clip(cards[seg["id"]], float(seg["duration"]),
                                CLIPS / f"{seg['id']}.mp4")
            dur = _probe_duration(clip)
            segments.append({"id": seg["id"], "kind": "card", "duration_s": round(dur, 2)})
            expected += dur
            clips.append(clip)
        else:
            src = Path(seg["path"])
            dur = _probe_duration(src)
            segments.append({"id": seg["id"], "kind": "video",
                             "duration_s": round(dur, 2), "source": src.name})
            expected += dur
            clips.append(src)

    _compose(clips, TOUR)
    total = _probe_duration(TOUR)
    video = {"path": TOUR.name, "duration_s": round(total, 2),
             "width": W, "height": H, "fps": FPS,
             "bytes": TOUR.stat().st_size if TOUR.exists() else 0}
    manifest = {
        "segments": segments,
        "expected_duration_s": round(expected, 2),
        "video": video,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    problems = validate_manifest(manifest)
    print(f"段落：{[s['id'] for s in segments]}")
    print(f"成片：{video['duration_s']}s（预期 {expected:.2f}s）/ {W}x{H}")
    print(f"输出：{TOUR}")
    print(f"清单：{MANIFEST}")
    if problems:
        for p in problems:
            print(f"  判据未过：{p}")
        return 1
    print("判据 A/B/C 通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
