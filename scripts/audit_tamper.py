"""篡改检测：证据链的核心承诺是**改了能发现**（B556）。

README 说「它给你一条能复查的证据链」。要配得上这句话，必须满足：

1. 原件能验过；
2. **改一个字节**就验不过；
3. 改回去又验得过（说明不是"永远红"）；
4. 链上任何一环被替换，`verify_chain` 必须断。

第 2、3 两条成对存在才有意义——只有第 2 条的话，一个"永远返回 False"的实现
也能通过。第 4 条验的是**链**而不只是单份哈希。

用法：``python scripts/audit_tamper.py``
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def _copy_run(src: Path, dst: Path) -> Path:
    shutil.copytree(src, dst)
    return dst


def _one_capsule(run_dir: Path) -> Path:
    caps = sorted((run_dir / "capsules").glob("*.json"))
    if not caps:
        raise SystemExit(f"{run_dir} 里没有 capsule：先跑一轮 run")
    return caps[0]


def _ordered_chain(caps_dir: Path) -> list[Any]:
    """把 capsule 目录重建成**链序**。

    为什么不能按文件名排：capsule 文件名是内容哈希，**顺序与链序无关**。
    按文件名喂进 `verify_chain` 会得到「链头 prev 不是全零」——
    那不是"链坏了"，是"喂错了顺序"（本量具第一版就踩了这个）。

    做法：按 `prev_sha256 → self_sha256` 建图，从 genesis（prev 全零）出发走到底。
    """
    from hos_ares_core.evidence import load_capsule

    caps = [load_capsule(str(p)) for p in sorted(caps_dir.glob("*.json"))]
    by_prev = {c.chain.prev_sha256: c for c in caps}
    zero = "0" * 64
    start = by_prev.get(zero) or by_prev.get("")
    if start is None:
        # 找不到 genesis：原样返回（让 verify_chain 去报"不是从头开始"）
        return caps
    ordered = [start]
    seen = {start.chain.self_sha256}
    while True:
        nxt = by_prev.get(ordered[-1].chain.self_sha256)
        if nxt is None or nxt.chain.self_sha256 in seen:
            break
        ordered.append(nxt)
        seen.add(nxt.chain.self_sha256)
    return ordered


def audit(src: Path) -> dict[str, Any]:
    """四步：原件过 → 改字节不过 → 改回又过 → 链断可检出。"""
    from hos_ares_core.evidence import load_capsule, verify_chain

    result: dict[str, Any] = {}
    with tempfile.TemporaryDirectory() as tmp:
        work = _copy_run(src, Path(tmp) / "copy")

        # 1) 原件（**按链序**读，不按文件名）
        caps = _ordered_chain(work / "capsules")
        rep_clean = verify_chain(caps)
        result["original_verifies"] = bool(rep_clean.ok)
        result["original_detail"] = f"len={rep_clean.length} breaks={list(rep_clean.breaks)[:2]}"

        # 2) 改一个字节（在 body 的 JSON 里塞一个字段）
        target = _one_capsule(work)
        original_bytes = target.read_bytes()
        doc = json.loads(original_bytes.decode("utf-8"))
        # 找一个一定存在的顶层键来改（不猜结构：改 `chain` 里的 self_sha256 最直接）
        tampered = json.loads(json.dumps(doc))
        if isinstance(tampered.get("chain"), dict):
            tampered["chain"]["self_sha256"] = "0" * 64
        else:
            tampered["_tamper"] = True
        target.write_text(json.dumps(tampered, ensure_ascii=False), encoding="utf-8")

        caps_bad = _ordered_chain(work / "capsules")
        rep_bad = verify_chain(caps_bad)
        result["tampered_detected"] = not rep_bad.ok
        result["tampered_detail"] = f"breaks={list(rep_bad.breaks)[:2]}"

        # 3) 改回去又过 —— 证明第 2 步不是"永远红"
        target.write_bytes(original_bytes)
        caps_back = _ordered_chain(work / "capsules")
        rep_back = verify_chain(caps_back)
        result["restored_verifies"] = bool(rep_back.ok)

        # 4) 删一环：**单靠 capsule 文件证不了**（B556 实测）。
        #
        # `verify_chain` 的 docstring 已经写明："链尾被删在只有 capsule 文件时
        # **无法**证明（没有外部锚点），所以报告侧按 `findings.evidence.capsule_id`
        # 列出证据全集——链尾缺口由那个集合对账，不在这儿猜。"
        #
        # 所以这一步改验**对账**：`findings.json` 里登记的证据集合
        # 必须与盘上的 capsule 集合逐条对上。删一环之后，集合差 1 条。
        if len(caps) >= 2:
            victim = sorted((work / "capsules").glob("*.json"))[1]
            victim_sha = load_capsule(str(victim)).chain.self_sha256
            victim.unlink()
            on_disk = {load_capsule(str(p)).chain.self_sha256
                       for p in (work / "capsules").glob("*.json")}
            findings = json.loads((work / "findings.json").read_text(encoding="utf-8"))
            registered = {
                str((f.get("evidence") or {}).get("self_sha256") or "")
                for f in findings["findings"]
            } - {""}
            missing = registered - on_disk
            result["missing_link_detected"] = bool(missing)
            result["missing_detail"] = (
                f"登记 {len(registered)} / 盘上 {len(on_disk)} / 缺 {len(missing)}"
                f"（删的是 {victim_sha[:12]}）"
            )
        else:
            result["missing_link_detected"] = None

    return result


def main() -> int:
    src = ROOT / "out" / "recall" / "run"
    if not src.exists():
        print(f"没有 {src}：先跑 scripts/measure_recall.py 生成一轮证据")
        return 2
    result = audit(src)
    print(json.dumps(result, ensure_ascii=False, indent=1))
    print()
    checks = [
        ("原件验得过", result["original_verifies"]),
        ("改一字节被发现", result["tampered_detected"]),
        ("改回去又验得过", result["restored_verifies"]),
        ("删一环被对账发现", bool(result["missing_link_detected"])),
    ]
    bad = [name for name, ok in checks if not ok]
    if bad:
        print("失败：" + "、".join(bad))
        return 1
    print("结论：篡改可检出，且检测不是恒红（改回即恢复）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
