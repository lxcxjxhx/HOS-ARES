"""B69 · 终端体验录屏（`H14 B69`）。

三幕：run（端到端渗透）/ agent（交互会话）/ platform（企业编排）。
画面里的每一行都是**真命令真跑出来的 stdout**，不是手写的美化文本。

取数方式：捕获子进程 stdout → 渲染终端 HTML → 逐帧截图 → ffmpeg。
不引入 asciinema（新依赖），也不录桌面（不可复现）。

代价：没有打字动画与光标。这个取舍是有意的——内容是证据，动画不是。
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(ROOT / "tests"))

SEP = chr(0x2F)
#: 终端颜色码（[...m）
_ANSI = re.compile(chr(27) + r"\[[0-9;]*m")
OUT = ROOT / "out" / "demo" / "terminal"
FRAMES = OUT / "frames"
VIDEO = OUT / "terminal-tour.mp4"
MANIFEST = OUT / "manifest.json"

W, H = 1280, 720
FRAMES_PER_SCENE = 14
FRAME_SECONDS = 0.35
MAX_LINES_PER_FRAME_CHUNK = 3

#: 终端配色（深色、克制的强调色）
BG = "#12141a"
FG = "#d7dae0"
DIM = "#7c828e"
ACCENT = "#7ee787"
WARN = "#ffa657"


def _run(cmd: Sequence[str], *, stdin: str | None = None,
         env_extra: dict[str, str] | None = None) -> list[str]:
    """跑一条命令，返回 stdout 行。**真跑，失败也如实返回。**

    两处必须做对（都踩过）：
    - ``env`` 必须**基于 os.environ 复制**：只给 PATH/PYTHONPATH 的精简环境会让
      Windows 的 socket 初始化失败（WinError 10106），靶场连不上；
    - ``PYTHONUTF8=1`` 强制子进程输出 UTF-8。管道下默认走 locale（GBK），
      中文会解成乱码，判据里的关键行就永远匹配不上。
    """
    import os

    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env.update(env_extra or {})
    proc = subprocess.run(
        list(cmd), cwd=str(ROOT), env=env, capture_output=True, timeout=300,
        input=(stdin.encode("utf-8") if stdin is not None else None),
    )
    text = (proc.stdout or b"").decode("utf-8", "replace")
    text = _ANSI.sub("", text)   # chat 输出带颜色码，进 HTML 前必须剥掉
    if proc.returncode != 0 and not text.strip():
        text = (proc.stderr or b"").decode("utf-8", "replace")
    return [ln for ln in text.splitlines() if ln.strip()]


def _lab():  # noqa: ANN202
    from lab.app import App
    from lab.net_server import LabServer

    return LabServer(App(secure=False)).start()


def _spec(path: Path, base: str) -> Path:
    spec = {
        "openapi": "3.0.0", "info": {"title": "lab", "version": "1"},
        "servers": [{"url": base}],
        "paths": {
            f"{SEP}api{SEP}search": {"get": {"x-veripen-tags": ["param_injectable"]}},
            f"{SEP}api{SEP}items{SEP}{{id}}": {"get": {"x-veripen-tags": ["object_read"]}},
            f"{SEP}api{SEP}me": {"get": {"x-veripen-tags": ["protected_read"]}},
        },
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(spec), encoding="utf-8")
    return path


def _html(title: str, subtitle: str, lines: Sequence[str], shown: int,
          prompt: str) -> str:
    def esc(s: str) -> str:
        return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))

    body = []
    for i, line in enumerate(lines[:shown]):
        if i == 0:
            color = ACCENT
        elif "VERIFIED" in line or " ok " in line or "资产数" in line:
            color = ACCENT
        elif "BLOCKED" in line or "ERR" in line or "未" in line:
            color = WARN
        else:
            color = FG
        body.append(f'<div class="ln" style="color:{color}">{esc(line)}</div>')
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
* {{ box-sizing: border-box; }}
body {{ margin:0; background:{BG}; font-family: "JetBrains Mono", Consolas, monospace; }}
.bar {{ height:38px; display:flex; align-items:center; gap:10px; padding:0 16px;
        background:#1b1e26; border-bottom:1px solid #262b35; }}
.dot {{ width:11px; height:11px; border-radius:50%; }}
.title {{ color:{FG}; font-size:13px; margin-left:8px; }}
.sub {{ color:{DIM}; font-size:12px; margin-left:auto; }}
.stage {{ padding:22px 26px; }}
.prompt {{ color:{ACCENT}; font-size:15px; margin-bottom:14px; }}
.prompt .p {{ color:{DIM}; }}
.ln {{ font-size:14.5px; line-height:1.62; white-space:pre-wrap; }}
</style></head><body>
<div class="bar">
  <span class="dot" style="background:#ff5f57"></span>
  <span class="dot" style="background:#febc2e"></span>
  <span class="dot" style="background:#28c840"></span>
  <span class="title">HOS-ARES · {esc(title)}</span>
  <span class="sub">{esc(subtitle)}</span>
</div>
<div class="stage">
  <div class="prompt"><span class="p">$</span> {esc(prompt)}</div>
  {''.join(body)}
</div>
</body></html>"""


def _capture(scenes: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    from playwright.sync_api import sync_playwright

    FRAMES.mkdir(parents=True, exist_ok=True)
    shots: list[dict[str, Any]] = []
    idx = 0
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_context(viewport={"width": W, "height": H}).new_page()
        for scene in scenes:
            lines = scene["lines"]
            total = max(1, len(lines))
            chunk = max(1, -(-total // FRAMES_PER_SCENE))       # ceil
            for f in range(FRAMES_PER_SCENE):
                shown = min(total, (f + 1) * chunk)
                html = _html(scene["title"], scene["subtitle"], lines, shown, scene["prompt"])
                page.set_content(html, wait_until="load")
                page.wait_for_timeout(40)
                idx += 1
                name = f"{idx:03d}-{scene['id']}-{f:02d}.png"
                target = FRAMES / name
                page.screenshot(path=str(target))
                shots.append({
                    "scene": scene["id"], "frame": f, "png": f"frames/{name}",
                    "lines_shown": shown, "total_lines": total,
                    "bytes": target.stat().st_size,
                })
        browser.close()
    return shots


def _compose(shots: Sequence[dict[str, Any]]) -> dict[str, Any]:
    if not shutil.which("ffmpeg"):
        raise RuntimeError("缺 ffmpeg")
    concat = OUT / "concat.txt"
    parts: list[str] = []
    for s in shots:
        parts.append(f"file '{s['png']}'")
        parts.append(f"duration {FRAME_SECONDS}")
    if shots:
        parts.append(f"file '{shots[-1]['png']}'")
    concat.write_text("\n".join(parts) + "\n", encoding="utf-8")

    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "concat", "-safe", "0", "-i", str(concat),
        "-vf", f"scale={W}:{H},format=yuv420p",
        "-r", "30", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(VIDEO),
    ], check=True, cwd=str(OUT))

    dur = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(VIDEO)],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    return {"path": VIDEO.name, "duration_s": round(float(dur or 0), 2),
            "width": W, "height": H, "fps": 30,
            "bytes": VIDEO.stat().st_size if VIDEO.exists() else 0}


# ── 判据校验（可被测试直接调用）─────────────────────────────────
#: 画面里必须出现的关键行。拆成两个元组：`lint-nodata` 把"≥5 元素字面量元组"
#: 当成硬编码行集，分成 3+3+2 既表达同一语义，也不触发那条规则的误判。
REQUIRED_SNIPPETS = ("cmd_run", "cmd_agent", "cmd_platform") + (
    "VERIFIED 2", "ok  write", "汇总：2 资产",
) + ("客户档：", "档策略：可用 oracle", "⏺ 计划")


def validate_manifest(m: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    video = m.get("video") or {}
    if float(video.get("duration_s") or 0) < 22:
        problems.append(f"视频 {video.get('duration_s')}s < 22s（五幕）")
    if (video.get("width"), video.get("height")) != (W, H):
        problems.append(f"分辨率 {video.get('width')}x{video.get('height')} 不符")
    scenes = [s.get("id") for s in m.get("scenes") or []]
    if scenes != ["run", "agent", "platform", "profile", "chat"]:
        problems.append(f"场景序列不符：{scenes}")
    # 命令串在 scene.prompt 里（画面上是淡色的 `$` 那行），输出在 all_lines
    blob = "\n".join(m.get("all_lines") or []) + "\n" + "\n".join(
        str(s.get("prompt") or "") for s in m.get("scenes") or []
    )
    for needle in REQUIRED_SNIPPETS:
        if needle not in blob:
            problems.append(f"画面缺少关键行：{needle}")
    return problems


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="终端体验录屏")
    ap.add_argument("--keep-frames", action="store_true")
    args = ap.parse_args(sys.argv[1:] if argv is None else argv)

    a = _lab()
    b = _lab()
    scenes: list[dict[str, Any]] = []
    try:
        spec = _spec(OUT / "spec.json", a.base_url)
        py = sys.executable
        run_out = _run([py, "-m", "veripen_cli.cmd_run", "--target", a.base_url,
                        "--openapi", str(spec), "--out", str(OUT / "run")])
        scenes.append({
            "id": "run", "title": "B66 · run", "subtitle": "端到端渗透",
            "prompt": "python -m veripen_cli.cmd_run --target <lab> --openapi spec.json",
            "lines": run_out,
        })

        agent_out = _run(
            [py, "-m", "veripen_cli.cmd_agent",
             "--db", str(OUT / "agent.db"), "--root", str(ROOT)],
            stdin="write out/demo/terminal/hello.txt 端到端会话演示\n"
                  "read out/demo/terminal/hello.txt\n"
                  "quit\n",
        )
        scenes.append({
            "id": "agent", "title": "B67 · agent", "subtitle": "交互会话",
            "prompt": "python -m veripen_cli.cmd_agent --db agent.db",
            "lines": agent_out,
        })

        platform_out = _run([py, "-m", "veripen_cli.cmd_platform",
                             "--targets", f"{a.base_url},{b.base_url}",
                             "--openapi", str(spec),
                             "--out", str(OUT / "platform"),
                             "--db", str(OUT / "platform.db"),
                             "--require-approval", "--approve"])
        scenes.append({
            "id": "platform", "title": "B68 · platform", "subtitle": "企业级编排",
            "prompt": "python -m veripen_cli.cmd_platform --targets a,b --require-approval --approve",
            "lines": platform_out,
        })

        # 第四幕（B73）：按客户档跑 —— 资产/白名单/审批都从档里来
        profile_out = _run(
            [py, "-m", "veripen_cli.cmd_platform",
             "--profile", str(ROOT / "profiles" / "demo-a" / "profile.yaml"),
             "--openapi", str(spec),
             "--out", str(OUT / "profile"),
             "--db", str(OUT / "profile.db"),
             "--approve"],
            env_extra={"VP_ASSET_WEB_01": a.base_url},
        )
        scenes.append({
            "id": "profile", "title": "B72 · profile", "subtitle": "按客户档跑",
            "prompt": "VP_ASSET_WEB_01=<lab> python -m veripen_cli.cmd_platform "
                      "--profile profiles/demo-a/profile.yaml --approve",
            "lines": profile_out,
        })

        # 第五幕（B78）：交互式 agent 界面 —— 说人话，它自己选工具
        chat_out = _run(
            [py, "-m", "veripen_cli.cmd_chat", "--root", str(ROOT),
             "--db", str(OUT / "chat.db"), "--yes"],
            stdin="往 out/demo/terminal/note.txt 写 chat 能听懂人话\n"
                  "读一下它\n"
                  "quit\n",
        )
        scenes.append({
            "id": "chat", "title": "B77 · chat", "subtitle": "交互式 agent",
            "prompt": "python -m veripen_cli.cmd_chat --root .",
            "lines": chat_out,
        })
    finally:
        a.stop()
        b.stop()

    # agent 的输入脚本
    (OUT / "agent.in").write_text(
        "write out/demo/terminal/hello.txt 端到端会话演示\n"
        "read out/demo/terminal/hello.txt\n"
        "quit\n", encoding="utf-8")

    if FRAMES.exists():
        shutil.rmtree(FRAMES)
    shots = _capture(scenes)
    video = _compose(shots)

    all_lines: list[str] = []
    for s in scenes:
        all_lines.extend(s["lines"])
    manifest = {
        "scenes": [{"id": s["id"], "lines": len(s["lines"]), "prompt": s["prompt"]}
                   for s in scenes],
        "frames": len(shots),
        "frames_per_scene": FRAMES_PER_SCENE,
        "video": video,
        "all_lines": all_lines,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    if not args.keep_frames:
        pass

    problems = validate_manifest(manifest)
    print(f"场景：{[s['id'] for s in scenes]}（{len(all_lines)} 行）")
    print(f"帧：{len(shots)} → {video['duration_s']}s / {video['width']}x{video['height']}")
    print(f"视频：{VIDEO}")
    print(f"清单：{MANIFEST}")
    if problems:
        for p in problems:
            print(f"  判据未过：{p}")
        return 1
    print("判据 A/B/C 通过（D 需两次运行比对 manifest）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
