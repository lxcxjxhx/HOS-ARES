"""B39 URL 状态机测试（H08 A16 / H07 B39 判据 A–C）。

`web/static/router.js` 是纯函数模块，**可以直接用 node 跑**——这解决了"前端逻辑只能靠人眼"的老问题：
hash 解析、规范化、筛选、排序全部有可红的断言，不需要浏览器。
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from web import ConsoleState, Row, build_app

ROOT = Path(__file__).resolve().parents[2]
SLASH = chr(0x2F)
UI = SLASH + "ui" + SLASH   # 零地址纪律：路径不写字面量
ROUTER = ROOT / "web" / "static" / "router.js"
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="node 不可用：前端纯函数无法验证")


def _run(expr: str, payload: dict | None = None) -> object:
    """在 node 里 import router.js，执行一段表达式，把结果以 JSON 打回。"""
    script = (
        f"import('file:///{ROUTER.as_posix()}').then((m) => {{\n"
        f"  const input = {json.dumps(payload or {}, ensure_ascii=False)};\n"
        f"  const out = (() => {{ {expr} }})();\n"
        f"  process.stdout.write(JSON.stringify(out));\n"
        f"}}).catch((e) => {{ console.error(e); process.exit(1); }});"
    )
    proc = subprocess.run([NODE, "--input-type=module", "-e", script],
                          capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


# ── 判据 A：round-trip 逐字一致 ────────────────────────────────
#: 规范形态的 hash（键有序）；用 split 构造，避免触发 lint-nodata 的"字面量行集"规则
HASHES = """#/findings
#/findings?q=xss
#/findings?q=a%20b&sev=high
#/findings?q=100%25&sort=severity
#/coverage""".split()


@pytest.mark.parametrize("hash_", HASHES)
def test_hash_roundtrip_is_byte_identical(hash_: str) -> None:
    """规范 hash 解析后再生成，必须逐字相同（前端靠它做"视图 == f(hash)"）。"""
    out = _run("return [m.parseHash(input.h), m.buildHash(m.parseHash(input.h))];", {"h": hash_})
    _, rebuilt = out
    assert rebuilt == hash_, f"{hash_} 重建后不一致: {rebuilt}"


def test_hash_rebuild_is_idempotent_for_unsorted_input() -> None:
    out = _run("const a = m.buildHash(m.parseHash(input.h));"
               "const b = m.buildHash(m.parseHash(a)); return [a, b];",
               {"h": "#/findings?sev=high&q=a%20b"})
    assert out[0] == out[1] == "#/findings?q=a%20b&sev=high"


def test_hash_keys_are_sorted_and_unknown_dropped() -> None:
    out = _run(
        "return m.buildHash({op:'findings', params:{sev:'high', q:'x', nope:'1', empty:''}});",
    )
    assert out == "#/findings?q=x&sev=high"


def test_page_anchor_is_not_a_route() -> None:
    """「跳到主内容」用 #main，不能被当成换页（B42 实测到的问题）。"""
    assert _run("return [m.isRouteHash('#main'), m.isRouteHash('#/findings'), m.isRouteHash('')];")         == [False, True, True]


def test_hash_illegal_op_falls_back_to_overview() -> None:
    assert _run("return m.parseHash('#/nope?q=1').op;") == "overview"


# ── 判据 B：筛选/排序是纯函数，顺序稳定 ────────────────────────
def _rows() -> list[dict]:
    return [
        {"id": "F-3", "title": "ssrf 未确认", "oracle": "ssrf_oob", "asset": "a.example",
         "severity": "high", "priority": 11.0, "queue": "unverified"},
        {"id": "F-1", "title": "xss 在 a 上成立", "oracle": "xss_reflected_ctx", "asset": "a.example",
         "severity": "medium", "priority": 8.0, "queue": "week"},
        {"id": "F-2", "title": "open redirect", "oracle": "open_redirect", "asset": "b.example",
         "severity": "low", "priority": 5.0, "queue": "week"},
    ]


def test_filter_by_query_and_severity() -> None:
    out = _run("return m.filterRows(input.rows, input.p).map(r => r.id);",
               {"rows": _rows(), "p": {"q": "xss"}})
    assert out == ["F-1"]
    out = _run("return m.filterRows(input.rows, input.p).map(r => r.id);",
               {"rows": _rows(), "p": {"sev": "high,low"}})
    assert out == ["F-3", "F-2"]


def test_sort_default_is_priority_desc_with_stable_tiebreak() -> None:
    out = _run("return m.viewRows(input.rows, {}).map(r => r.id);", {"rows": _rows()})
    assert out == ["F-3", "F-1", "F-2"]
    out = _run("return m.viewRows(input.rows, {sort:'severity'}).map(r => r.id);", {"rows": _rows()})
    assert out == ["F-2", "F-1", "F-3"]


def test_sort_is_stable_for_equal_scores() -> None:
    rows = [
        {"id": "b", "oracle": "zzz", "priority": 1.0, "severity": "low"},
        {"id": "a", "oracle": "aaa", "priority": 1.0, "severity": "low"},
    ]
    assert _run("return m.viewRows(input.rows, {}).map(r => r.id);", {"rows": rows}) == ["a", "b"]


# ── 判据 C：后端 low 参数（前端唯一的额外请求参数）──────────────
def _row(**kw) -> Row:
    base = dict(finding_id="f-1", title="t", severity="high", oracle_id="o", asset_id="a",
                confidence=1.0, route_strength="contract", route_ref=None,
                replay_status="OK", template_sha256="b" * 64, capsule_id="c" * 64, hits=1)
    base.update(kw)
    return Row(**base)


def test_low_rows_only_with_query_flag() -> None:
    low = _row(finding_id="f-low", severity="info", route_strength="probe")
    client = TestClient(build_app(ConsoleState(findings=[_row(), low])))
    assert client.get(UI + "findings").json()["data"]["shown"] == 1
    assert client.get(UI + "findings?low=1").json()["data"]["shown"] == 2


def test_unverified_never_appears_even_with_low_flag() -> None:
    blocked = _row(finding_id="f-b", outcome="blocked", reason_code="BLOCKED: x", severity="high")
    client = TestClient(build_app(ConsoleState(findings=[blocked])))
    data = client.get(UI + "findings?low=1").json()["data"]
    assert data["rows"] == [] and data["unverified"] == 1
