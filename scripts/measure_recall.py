"""靶场召回量具（B549）：**靶场上可判定的面，实际被判定了多少**。

为什么需要它：`H05 §2` 里的「真实资产召回 0.33」测的是 Firing Range 这类**外部**目标，
本机访问不到，于是那个数字一直只能原样引用。但有一件**本机可测**的等价物：

    靶场自己声明了 N 个面（`app.assets_json()`），其中 M 个是本产品**明确能判**的
    （不是"所有面"，写类默认排除、需要 OOB 的拿不到回调）。跑一轮之后，看这 M 个里
    有多少真的产出了判定。

这不是"召回 0.33"的替身——它测的是**同一条链路的另一半**：
外部目标召回低有三条原因（OOB 缺域名 / 端点不匹配 / 标签没被采纳），
后两条在靶场上不存在（靶场是配套的、标签是齐的）。于是这条量具回答的是：

    在"目标配合"的前提下，这条链路自己能拿到多少？

那个数如果高，说明召回 0.33 的主因在目标侧；如果低，说明问题在我们自己。
**两种结论都要能说**，所以先把它测出来。

用法：``python scripts/measure_recall.py``
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
for extra in (ROOT, ROOT / "tests"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))


def _mounts_of(assets: dict[str, Any]) -> dict[str, tuple[str, ...]]:
    """面 → 它能触发的 selector 标签。用于把"能判的面"与"判定结果"对上。"""
    return {
        str(op["template"]): tuple(op.get("tags") or ())
        for op in assets.get("operations", [])
    }


def measure(base_url: str, *, out_dir: Path, with_credentials: bool = True,
            requests: int = 2000) -> dict[str, Any]:
    """跑一轮契约路径，回来算靶场上的面覆盖率。

    ``with_credentials=True`` 时把靶场**自己知道**的两样东西交上去：

    - **token**（`lab.app.make_token`）：认证类 oracle 需要它，不给就全落
      `baseline_abnormal`——那不是"判定不了"，是"没带身份"；
    - **符号表**（`SAFE_TARGET` / `STRANGER` / `SAFE_FILE` / `DEAD_HOST`）：
      OOB 与 SSRF 类的 `{{ref:…}}` 全靠它们，缺了就是 `symbol_missing`。

    两样都是**部署时注入的**（N3），不是写进代码的地址。这里由靶场提供，
    正是"目标配合"该有的样子。
    """
    from lab.app import App, assets_json, make_token
    from lab.contract import to_openapi
    from hos_ares_cli.__main__ import main

    assets = assets_json()
    out_dir.mkdir(parents=True, exist_ok=True)
    spec = out_dir / "openapi.json"
    spec.write_text(
        json.dumps(to_openapi(assets, base_url=base_url), ensure_ascii=False), encoding="utf-8"
    )
    run_dir = out_dir / "run"
    extra: list[str] = []
    if with_credentials:
        extra += ["--token", make_token("primary"), "--token-secondary", make_token("stranger")]
        symbols = {
            "SAFE_TARGET": base_url,
            "STRANGER": "stranger",
            "SAFE_FILE": "notes.txt",
            # 死地址：靶场不会去连它，任何解析结果都说明"这一步真的执行了"
            "DEAD_HOST": "dead.invalid",
        }
        symbols_path = out_dir / "symbols.json"
        symbols_path.write_text(json.dumps(symbols), encoding="utf-8")
        extra += ["--symbols-file", str(symbols_path)]
    # 24 个面 × 多 oracle 会撞满默认 200 的请求预算（B549）
    extra += ["--max-requests", str(requests)]
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        code = main(["run", "--target", base_url, "--openapi", str(spec),
                     "--out", str(run_dir), *extra])

    findings = json.loads((run_dir / "findings.json").read_text(encoding="utf-8"))
    rows = findings["findings"]

    # 面 → 是否至少产出一条 reported（按 route.ref 的短形式对不上模板，
    # 所以用 **findings 里出现过的 oracle 集合** 与 **目标面能触发的标签** 求交）
    tags_by_face = _mounts_of(assets)
    all_faces = len(tags_by_face)
    reported_oracles = {f["oracle"] for f in rows if f.get("outcome") == "reported"}

    from hos_ares_oracles import ALL_ORACLES

    #: oracle → 它能选的标签集合
    oracle_tags = {
        c.meta.id: set(c.meta.selector_tags) for c in ALL_ORACLES if c.meta.selector_tags
    }
    target_tags = {t for tags in tags_by_face.values() for t in tags}
    #: 靶场上**有面可判**的 oracle：它的 selector 与靶场标签有交集
    reachable = {o for o, ts in oracle_tags.items() if ts & target_tags}
    judged = reachable & reported_oracles

    return {
        "exit_code": code,
        "with_credentials": with_credentials,
        "faces_total": all_faces,
        "oracles_reachable": sorted(reachable),
        "oracles_judged": sorted(judged),
        "recall_on_lab": round(len(judged) / len(reachable), 3) if reachable else 0.0,
        "reported_total": len(reported_oracles),
        "by_outcome": findings["counts"]["by_outcome"],
        "missed": sorted(reachable - reported_oracles),
    }


def main() -> int:
    from lab.app import App
    from lab.net_server import serve

    gen = serve(App())
    base = next(gen)
    try:
        out = ROOT / "out" / "recall"
        out.mkdir(parents=True, exist_ok=True)
        result = measure(base, out_dir=out)
    finally:
        with contextlib.suppress(StopIteration):
            next(gen)

    print(json.dumps(result, ensure_ascii=False, indent=1))
    print()
    print(f"靶场面数：{result['faces_total']}")
    print(f"可达 oracle：{len(result['oracles_reachable'])}")
    print(f"真判定：{len(result['oracles_judged'])}")
    print(f"**靶场召回：{result['recall_on_lab']:.3f}**")
    if result["missed"]:
        print(f"未判定：{', '.join(result['missed'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
