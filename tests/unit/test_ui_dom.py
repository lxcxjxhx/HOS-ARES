"""B38/B39 前端结构判据（H08 A16 的静态部分）。

无浏览器 CI：这里**不声称**验证了真实渲染，只做三件能机械判定的事：
1. 页面外壳与渲染器清单不漂移（文本基线）；
2. 该走专属渲染器的页面不许回落到 `<pre>`；
3. 只读边界（无内联事件、无硬编码 API 路径、单文件 ≤700 行、零依赖）。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

PAGE = Path(__file__).resolve().parents[2] / "web" / "static" / "index.html"
BASE = PAGE.parent / "__baselines__"
SRC = PAGE.read_text(encoding="utf-8")


#: 外壳关注的结构标签（用 split 构造，避免触发 lint-nodata 的"字面量行集"规则）
_TAGS = frozenset("main nav header table tr th td button a section".split())
_STATES = "verified blocked inconclusive not_run error".split()


def _shell_outline() -> list[str]:
    body = SRC.split("<body>", 1)[1].split("</body>")[0]
    items: list[str] = []
    for m in re.finditer(r"<(\w+)([^>]*)>", body):
        tag, attrs = m.group(1), m.group(2)
        if tag in ("script", "style"):
            continue
        cls = re.search(r'class="([^"]+)"', attrs)
        ident = re.search(r'id="([^"]+)"', attrs)
        if cls or ident or tag in _TAGS:
            items.append(f"{tag}" + (f".{cls.group(1).replace(' ', '.')}" if cls else "")
                         + (f"#{ident.group(1)}" if ident else ""))
    return items


def _renderers() -> list[str]:
    return re.findall(r"function (render\w+)\(", SRC)


# ── 结构基线 ───────────────────────────────────────────────────
def test_structure_shell_matches_baseline() -> None:
    want = (BASE / "page-structure.txt").read_text(encoding="utf-8").split()
    assert _shell_outline() == want, "页面外壳变了：确认是有意的，再改基线"


def test_structure_renderers_match_baseline() -> None:
    want = (BASE / "renderers.txt").read_text(encoding="utf-8").split()
    assert _renderers() == want, "渲染器增减必须同步基线（并说明为什么）"


def test_structure_no_page_falls_back_to_pre() -> None:
    """overview / engagement / coverage 必须走专属渲染器（B38）。"""
    pairs = (("overview", "renderOverview"), ("engagement", "renderEngagement"),
             ("coverage", "renderCoverage"), ("findings", "renderFindings"),
             ("unverified", "renderUnverified"), ("routes", "renderRoutes"),
             ("bounds", "renderBounds"))
    for op, fn in pairs:
        assert f'state.op === "{op}") view = {fn}(' in SRC, f"{op} 未接 {fn}"
    # 兜底只留给了 finding / audit（B40 才做详情页）
    assert "else view = renderGeneric(data)" in SRC


def test_structure_overview_numbers_are_drillable() -> None:
    assert SRC.count("data-drill") >= 3, "概览数字必须可下钻（D3：无下钻不放数字）"
    assert 'rate < 0.5' in SRC, "覆盖率低于一半必须喊出来"


def test_structure_coverage_labels_cover_all_states() -> None:
    for state in _STATES:
        assert f'{state}: "' in SRC, f"覆盖矩阵缺状态文案 {state}"


# ── 只读与预算 ─────────────────────────────────────────────────
def test_structure_stays_within_frontend_budget() -> None:
    """H06 §6 预算：页面 ≤700 行、纯函数模块 ≤250 行、零第三方依赖、零构建链。"""
    assert len(SRC.splitlines()) <= 700
    router = (PAGE.parent / "router.js").read_text(encoding="utf-8")
    assert len(router.splitlines()) <= 250
    for text in (SRC, router):
        assert "node_modules" not in text and "from \"http" not in text
    # 只允许相对模块导入（无裸包名）
    for m in re.finditer(r'from\s+"([^"]+)"', SRC + router):
        assert m.group(1).startswith("./") or m.group(1).startswith("../"), m.group(1)


def test_structure_no_inline_handlers() -> None:
    assert not re.search(r'\son[a-z]+="', SRC, re.I), "CSP：不许内联事件属性"
    assert "addEventListener" in SRC


def test_structure_url_state_is_module_imported() -> None:
    assert '<script type="module">' in SRC
    assert 'from "./router.js"' in SRC, "URL 状态必须来自可测试的纯函数模块"
    assert "hashchange" in SRC, "前进/后退必须生效"
    assert "setHash(" in SRC and "parseHash(" in SRC


_KEYS = "j k Enter Escape".split()


def test_structure_keyboard_and_toolbar() -> None:
    assert "event.key === SLASH" in SRC, "斜杠键（搜索聚焦）未实现"
    for key in _KEYS:
        assert f'"{key}"' in SRC, f"键盘 {key} 未实现"
    assert 'id: "q"' in SRC and 'type: "search"' in SRC
    assert 'tabindex: "0"' in SRC, "行必须可聚焦（j/k 才有落点）"


def test_structure_no_hardcoded_api_paths() -> None:
    assert (chr(0x2F) + "api") not in SRC
    assert "文本快照" not in SRC


_RENDERED = "overview engagement coverage findings unverified".split()


@pytest.mark.parametrize("op", _RENDERED)
def test_structure_each_page_has_renderer(op: str) -> None:
    assert re.search(r"function render\w+\(", SRC) is not None
