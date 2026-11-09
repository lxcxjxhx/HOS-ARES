"""B183：平台多资产时，控制台只能看合计——页面上没有按资产筛的入口。

判据：`web/static/router.js` 早就把 `asset` 当精确匹配参数了（`PARAMS` 里有它，
`filterRows` 里也判它），服务端却既不认这个参数、也不回资产清单：
于是没人能选出"只看 asset-2"。

本轮补两块：
* 服务端：`ui.findings` 认 `asset=`，并回 `assets`（本数据集里出现过的资产）；
* 页面：**超过一个资产**才画筛选器（只有一个时它只是噪声），选中态写回 hash。

这里钉的是服务端口径与"该不该画筛选器"的判据。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for extra in (ROOT, ROOT / "tests"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))


def _rows(assets: list[str]):  # noqa: ANN202
    from web.console import Row

    rows = []
    for i, asset in enumerate(assets):
        rows.append(Row(
            finding_id=f"{asset}-F{i}",
            title=f"{asset} 的发现",
            severity="high",
            oracle_id="csp_absent",
            asset_id=asset,
            confidence=0.9,
            route_strength="strong",
            route_ref="r" + str(i),
            replay_status="OK",
            outcome="reported",
        ))
    return rows


def test_assets_are_listed_and_filter_narrows_rows() -> None:
    from web.console import findings_view

    rows = _rows(["asset-1", "asset-2"])
    everything = findings_view(rows)
    assert everything["assets"] == ["asset-1", "asset-2"]
    assert everything["shown"] == 2 and everything["asset"] == ""

    only = findings_view(rows, asset="asset-2")
    assert only["shown"] == 1
    assert [r["asset"] for r in only["rows"]] == ["asset-2"]
    # 总数仍是全部：读者要能看出"筛掉了多少"
    assert only["total"] == 2


def test_single_asset_dataset_has_no_reason_for_a_filter() -> None:
    from web.console import findings_view

    view = findings_view(_rows(["asset-1"]))
    assert view["assets"] == ["asset-1"], "只有一个资产时页面不该画筛选器"


def test_unknown_asset_yields_empty_but_not_an_error() -> None:
    from web.console import findings_view

    view = findings_view(_rows(["asset-1"]), asset="asset-404")
    assert view["shown"] == 0 and view["rows"] == []
    assert view["assets"] == ["asset-1"], "资产清单不随筛选变"


def test_page_forwards_asset_in_the_query_map() -> None:
    """页面只转发白名单里的参数；`asset` 不在里面就永远传不到服务端。"""
    html = (ROOT / "web" / "static" / "index.html").read_text(encoding="utf-8")
    assert 'findings: ["low", "asset"]' in html
    assert 'id: "asset"' in html, "筛选器控件没画出来"


# ── B184：筛选之后，数字要跟着筛 ────────────────────────────────
def _mixed():  # noqa: ANN202
    """asset-1：一条未验证；asset-2：一条未验证；asset-1：一条已确认。"""
    from web.console import Row

    def row(fid: str, asset: str, outcome: str):  # noqa: ANN202
        return Row(finding_id=fid, title="t", severity="high", oracle_id="csp_absent",
                   asset_id=asset, confidence=0.9, route_strength="strong",
                   route_ref="r", replay_status="OK", outcome=outcome,
                   reason_code="" if outcome == "reported" else "missing_input:auth:secondary")

    return [row("a1", "asset-1", "undetermined"), row("a2", "asset-2", "undetermined"),
            row("a3", "asset-1", "reported")]


def test_findings_counts_are_scoped_and_keep_the_global_figure() -> None:
    from web.console import findings_view

    view = findings_view(_mixed(), asset="asset-1")
    assert view["scoped"] is True
    assert view["shown"] == 1, "只算本资产的已确认行"
    assert view["unverified_scoped"] == 1, "本资产未验证 1 条"
    assert view["unverified"] == 2, "全局仍是 2 条——两个口径都要给"
    assert view["total"] == 3


def test_unverified_page_filters_and_reports_both_scopes() -> None:
    from web.console import unverified_view

    rows = [r for r in _mixed() if r.outcome != "reported"]
    view = unverified_view(rows, asset="asset-1")
    assert view["scoped"] is True
    assert view["counts"] == {"undetermined": 1, "error": 0, "not_run": 0}
    assert view["totals"] == {"total": 2, "unverified": 2}
    assert view["assets"] == ["asset-1", "asset-2"], "资产清单不随筛选变"


def test_unverified_page_without_filter_lists_everything() -> None:
    from web.console import unverified_view

    rows = [r for r in _mixed() if r.outcome != "reported"]
    view = unverified_view(rows)
    assert view["scoped"] is False and view["counts"]["undetermined"] == 2
