"""B167：控制台要能打开 `veripen platform` 的产物。

平台是"多资产"那个出口，产物形状是 `<root>/asset-N/findings.json`。
控制台原来只能开单资产目录：把 `out/platform` 指过去，页面是空的。

这里钉三件事：
1. 两个资产的行都在，且**不撞 id**（撞了详情页会串到另一个资产）；
2. 每行带资产名（结论表里看得出是哪台）；
3. 每行的证据路径按**它自己那个资产**的 capsules 解析（不跨资产借证据）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for extra in (ROOT, ROOT / "tests", ROOT / "tests" / "unit"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))


def _asset(root: Path, name: str, oracle: str, *, with_capsule: bool) -> str:
    import capsule_fixture as F

    from veripen_core.evidence import save_capsule, seal_capsule_doc

    sub = root / name
    sub.mkdir(parents=True)
    cap = seal_capsule_doc(F.capsule())
    cid = cap.capsule_id
    if with_capsule:
        save_capsule(cap, sub / "capsules")
    doc = {
        "schema": "veripen.report/v1",
        "engagement": name,
        "findings": [{
            "id": "F-1", "oracle": oracle, "title": oracle, "severity": "high",
            "confidence": 0.9, "asset": name,
            "route": {"ref": "route://contract/" + "c" * 16, "strength": "contract"},
            "evidence": {"capsule_id": cid},
            "outcome": "reported", "reason_code": "",
        }],
    }
    (sub / "findings.json").write_text(json.dumps(doc), encoding="utf-8")
    return cid


def test_platform_dir_merges_assets_without_id_collisions(tmp_path: Path) -> None:
    import capsule_fixture as F  # noqa: F401  （确保 fixture 模块可导入）

    from web.loader import load_file, summarize

    c1 = _asset(tmp_path / "pf", "asset-1", "csp_absent", with_capsule=True)
    c2 = _asset(tmp_path / "pf", "asset-2", "xss_dom", with_capsule=True)

    state = load_file(tmp_path / "pf")
    assert len(state.findings) == 2
    ids = [r.finding_id for r in state.findings]
    assert len(set(ids)) == 2, ids
    assert all(i.startswith("asset-") for i in ids)

    by_asset = {r.asset_id: r for r in state.findings}
    assert set(by_asset) == {"asset-1", "asset-2"}
    assert by_asset["asset-1"].capsule_id == c1
    assert by_asset["asset-2"].capsule_id == c2
    # 证据路径各自解析：不许跨资产借
    assert by_asset["asset-1"].capsule_path.endswith(c1.split(":")[-1] + ".json")
    assert "asset-1" in by_asset["asset-1"].capsule_path
    assert "asset-2" in by_asset["asset-2"].capsule_path

    stats = summarize(state)
    assert stats["total"] == 2 and stats["verified"] == 2
    assert state.engagement["asset"] == "2 个资产"


def test_asset_order_is_numeric_not_lexicographic(tmp_path: Path) -> None:
    """`asset-10` 必须排在 `asset-2` 后面（字典序会把它排前面）。"""
    import capsule_fixture as F  # noqa: F401

    from web.loader import load_file

    for name in ("asset-1", "asset-2", "asset-10"):
        _asset(tmp_path / "pf", name, "csp_absent", with_capsule=False)
    state = load_file(tmp_path / "pf")
    assert [r.asset_id for r in state.findings] == ["asset-1", "asset-2", "asset-10"]


def test_plain_run_dir_is_not_mistaken_for_platform(tmp_path: Path) -> None:
    import capsule_fixture as F  # noqa: F401

    from web.loader import load_file

    _asset(tmp_path / "run", "asset-1", "csp_absent", with_capsule=False)
    # 把 findings.json 也放到顶层：这是单资产目录的形状，按原路径加载
    (tmp_path / "run" / "findings.json").write_text(
        (tmp_path / "run" / "asset-1" / "findings.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    state = load_file(tmp_path / "run")
    assert [r.finding_id for r in state.findings] == ["F-1"], "顶层产物优先，不该被合并"
