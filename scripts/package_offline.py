#!/usr/bin/env python3
"""B13 · 离线包 CLI（A3「内网能装」）。

用法：
    python scripts/package_offline.py --version 1.0.0 --out dist/veripen-1.0.0-offline
    python scripts/package_offline.py --verify dist/veripen-1.0.0-offline
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from veripen_cli.offline import PackageError, build, verify  # noqa: E402


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="离线包构建 / 校验")
    ap.add_argument("--version", default="1.0.0")
    ap.add_argument("--out", default="")
    ap.add_argument("--src", default=str(ROOT / "packaging" / "offline"))
    ap.add_argument("--profiles", default=str(ROOT / "profiles"))
    ap.add_argument("--verify", default="", help="只校验已有包")
    ap.add_argument("--wheels-from", default="",
                    help="已有 *.whl 的目录（构建机先 pip download，再指过来）")
    ap.add_argument("--profile", default="light", choices=("light", "browser"),
                    help="包形态：light 只带内核；browser 额外带渲染执行体")
    args = ap.parse_args(argv)

    stdout = sys.stdout
    reconfigure = getattr(stdout, "reconfigure", None)
    if callable(reconfigure):
        reconfigure(encoding="utf-8", errors="replace")

    if args.verify:
        res = verify(Path(args.verify))
        print(res.render())
        return 0 if res.ok else 1

    out = Path(args.out or ROOT / "dist" / f"veripen-{args.version}-offline")
    profiles: list[Path] = []
    pdir = Path(args.profiles)
    if pdir.is_dir():
        profiles = [p for p in sorted(pdir.iterdir()) if p.is_dir()]

    try:
        manifest = build(src=Path(args.src), out=out, version=args.version,
                         extra_profiles=profiles, profile=args.profile,
                         wheels_from=Path(args.wheels_from) if args.wheels_from else None)
    except PackageError as exc:
        print(f"组包失败：{exc}")
        return 1

    total = sum(e.size for e in manifest.entries)
    print(f"已生成 {out}（形态 {manifest.profile}）")
    print(f"  文件 {len(manifest.entries)} 个，共 {total} 字节")
    for note in manifest.notes:
        print(f"  注意：{note}")

    res = verify(out)
    print(res.render())
    return 0 if res.ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
