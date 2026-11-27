"""B194：这几道 lint 到底有没有牙？

判据：`noroute`（地址）/ `nodata_stub`（桩数据）/ `netimports`（出网库）三处闸门
一直在 CI 里"0 命中"。0 命中既可能是"代码干净"，也可能是"规则根本没匹配上"——
后者更可怕：它每一轮都给你一个绿色的 0。

这组用例给每道闸门喂**已知违规样本**（必须命中）与**已知干净样本**（必须不命中）。
两条一起过，才叫"这道闸门是活的"。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
LINT = ROOT / "scripts" / "lint"
for extra in (ROOT, LINT):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

SEP = chr(0x2F)


def test_noroute_flags_a_hardcoded_address(tmp_path: Path) -> None:
    import noroute  # type: ignore[import-not-found]

    # 闸门只扫产品目录（含 web）：样本要放在里面
    bad = tmp_path / "hos_ares_core" / "bad.py"
    bad.parent.mkdir(parents=True, exist_ok=True)
    bad.write_text('TARGET = "http' + ":" + SEP + SEP + 'example.invalid"\n',
                   encoding="utf-8")
    hits = noroute._scan(tmp_path)
    assert hits, "把地址写进代码居然没命中——这道闸门没牙"


def test_noroute_passes_a_clean_file(tmp_path: Path) -> None:
    import noroute

    good = tmp_path / "hos_ares_core" / "good.py"
    good.parent.mkdir(parents=True, exist_ok=True)
    good.write_text('SEP = chr(0x2F)\nPATH = "web" + SEP + "static"\n', encoding="utf-8")
    assert noroute._scan(tmp_path) == [], "干净样本不该被误伤"


def test_nodata_flags_a_compound_name_in_product_code(tmp_path: Path,
                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    """`FakeTransport` / `fake_runner` 是真实命名形态，必须抓住（B194）。"""
    import nodata_stub  # type: ignore[import-not-found]

    root = tmp_path
    (root / "hos_ares_core").mkdir(parents=True)
    bad = root / "hos_ares_core" / "bad.py"
    word = "Fa" + "ke"
    bad.write_text(f"class {word}Transport:\n    pass\n", encoding="utf-8")
    monkeypatch.setattr(nodata_stub, "ROOT", root)
    assert nodata_stub._check_banned([bad]), "复合命名没命中——闸门等于没牙"


def test_nodata_still_flags_a_bare_fake_in_tests(tmp_path: Path,
                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    import nodata_stub

    root = tmp_path
    (root / "tests").mkdir(parents=True)
    bad = root / "tests" / "bad.py"
    # 禁用词本身要拼出来：这条 lint 也扫测试代码
    word = "fa" + "ke"
    bad.write_text(f"def {word}(argv):\n    return 0\n", encoding="utf-8")
    monkeypatch.setattr(nodata_stub, "ROOT", root)
    assert nodata_stub._check_banned([bad]), "整词形式在测试里也该拦"


def test_nodata_allows_test_helper_names(tmp_path: Path,
                                         monkeypatch: pytest.MonkeyPatch) -> None:
    """`_fake_run` 这类测试替身命名是允许的——只对产品代码用严格口径。"""
    import nodata_stub

    root = tmp_path
    (root / "tests").mkdir(parents=True)
    ok = root / "tests" / "ok.py"
    word = "fa" + "ke"
    ok.write_text(f"def _{word}_run(argv):\n    return 0\n", encoding="utf-8")
    monkeypatch.setattr(nodata_stub, "ROOT", root)
    assert nodata_stub._check_banned([ok]) == []


def test_nodata_passes_clean_names(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import nodata_stub

    (tmp_path / "hos_ares_core").mkdir(parents=True)
    good = tmp_path / "hos_ares_core" / "good.py"
    good.write_text("def record_runner():\n    return 1\n", encoding="utf-8")
    monkeypatch.setattr(nodata_stub, "ROOT", tmp_path)
    assert nodata_stub._check_banned([good]) == []


def test_netimports_flags_a_network_library(tmp_path: Path) -> None:
    import netimports  # type: ignore[import-not-found]

    bad = tmp_path / "bad.py"
    bad.write_text("import requests\n", encoding="utf-8")
    assert netimports.forbidden_imports_in(bad), "标准出网库没有命中"


def test_netimports_passes_stdlib_only(tmp_path: Path) -> None:
    import netimports

    good = tmp_path / "good.py"
    good.write_text("import json\nimport socket\n", encoding="utf-8")
    assert netimports.forbidden_imports_in(good) == []


# ── B195：spec_drift 的证据指针必须指向真东西 ──────────────────
def test_spec_drift_evidence_points_at_a_real_test() -> None:
    """I1/I12 原来指向不存在的用例名，而闸门只查"有没有写"。（已修）"""
    import spec_drift  # type: ignore[import-not-found]

    problems = {inv: spec_drift.evidence_problem(ptr)
                for inv, ptr in spec_drift.INVARIANT_EVIDENCE.items()}
    broken = {k: v for k, v in problems.items() if v}
    assert not broken, f"证据指向不存在的东西：{broken}"


def test_spec_drift_flags_a_missing_file_and_a_missing_symbol() -> None:
    import spec_drift

    assert "不存在" in spec_drift.evidence_problem("tests/unit/no_such_file.py")
    assert "不在该文件" in spec_drift.evidence_problem(
        "tests/unit/test_contracts.py::no_such_test"
    )
    # DDL: 标记只校验文件存在
    assert spec_drift.evidence_problem("hos_ares_core/store.py::DDL:blackboard_facts") == ""


# ── B196：lint-ui 也要能红 ─────────────────────────────────────
def _ui_root(tmp_path: Path) -> Path:
    web = tmp_path / "web"
    (web / "static").mkdir(parents=True)
    return web


def test_ui_zero_address_flags_an_address(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                          capsys: pytest.CaptureFixture[str]) -> None:
    import ui_zero_address  # type: ignore[import-not-found]

    web = _ui_root(tmp_path)
    (web / "static" / "index.html").write_text(
        '<a href="http' + ":" + SEP + SEP + 'evil.example">x</a>\n', encoding="utf-8"
    )
    monkeypatch.setattr(ui_zero_address, "WEB_DIR", web)
    monkeypatch.setattr(ui_zero_address, "ROOT", tmp_path)
    assert ui_zero_address.main() == 1, "页面里写死地址居然不红"
    assert "地址" in capsys.readouterr().out


def test_ui_zero_address_flags_a_loopback_and_a_port(tmp_path: Path,
                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    import ui_zero_address

    web = _ui_root(tmp_path)
    loop = "local" + "host"
    (web / "static" / "app.js").write_text(
        f'const host = "{loop}' + ':8080";\n', encoding="utf-8"
    )
    monkeypatch.setattr(ui_zero_address, "WEB_DIR", web)
    monkeypatch.setattr(ui_zero_address, "ROOT", tmp_path)
    assert ui_zero_address.main() == 1


def test_ui_zero_address_flags_clipboard_and_selectable(tmp_path: Path,
                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    """能复制就等于能把地址带走（34 §3）：剪贴板 API 与可选中文本都算问题。"""
    import ui_zero_address

    web = _ui_root(tmp_path)
    (web / "static" / "app.js").write_text(
        "navigator.clipboard.writeText(x);\n", encoding="utf-8"
    )
    monkeypatch.setattr(ui_zero_address, "WEB_DIR", web)
    monkeypatch.setattr(ui_zero_address, "ROOT", tmp_path)
    assert ui_zero_address.main() == 1

    (web / "static" / "app.js").write_text("td { user-select: text; }\n", encoding="utf-8")
    assert ui_zero_address.main() == 1


def test_ui_zero_address_passes_route_refs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                           capsys: pytest.CaptureFixture[str]) -> None:
    import ui_zero_address

    web = _ui_root(tmp_path)
    (web / "static" / "index.html").write_text(
        '<span data-ref="route' + ":" + SEP + SEP + 'contract/0a1b">x</span>\n', encoding="utf-8"
    )
    monkeypatch.setattr(ui_zero_address, "WEB_DIR", web)
    monkeypatch.setattr(ui_zero_address, "ROOT", tmp_path)
    assert ui_zero_address.main() == 0, capsys.readouterr().out


# ── B434：`lint-ui`（操作台零地址，34 §1）也要证明它有牙 ─────────────
def _ui_scan(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: str) -> int:
    import ui_zero_address

    web = tmp_path / "web" / "static"
    web.mkdir(parents=True, exist_ok=True)
    (web / "page.js").write_text(body, encoding="utf-8")
    monkeypatch.setattr(ui_zero_address, "WEB_DIR", web)
    monkeypatch.setattr(ui_zero_address, "ROOT", tmp_path)
    # main() 来自 scripts/lint/（无类型存根）→ 显式收口成 int（B589）
    code: int = ui_zero_address.main()
    return code


def test_ui_zero_address_flags_a_hardcoded_url(tmp_path: Path,
                                               monkeypatch: pytest.MonkeyPatch) -> None:
    url = "http" + ":" + SEP + SEP + "example.invalid" + SEP + "x"
    assert _ui_scan(tmp_path, monkeypatch, f'const u = "{url}";\n') == 1, "写死地址居然没红"


def test_ui_zero_address_flags_clipboard_api(tmp_path: Path,
                                             monkeypatch: pytest.MonkeyPatch) -> None:
    assert _ui_scan(tmp_path, monkeypatch,
                    "navigator.clipboard.writeText(x);\n") == 1, "剪贴板 API 居然没红"


def test_ui_zero_address_passes_route_ref(tmp_path: Path,
                                          monkeypatch: pytest.MonkeyPatch) -> None:
    body = 'const u = "route' + ":" + SEP + SEP + 'finding-42";\n// 只允许不透明引用\n'
    assert _ui_scan(tmp_path, monkeypatch, body) == 0, "干净样本被误伤"


# ── B435：`lint-policy`（默认关闭必须真关着）也要证明它有牙 ──────────
def test_policy_check_flags_a_leaked_default_off(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """若有 default_off 的 oracle 没被挡住，这道闸门必须红——否则它就是一句口号。"""
    import policy_check  # type: ignore[import-not-found]

    class _Leaked:
        blocked: dict[str, str] = {}          # 一个都没挡

    monkeypatch.setattr(policy_check.policy, "check_enabled",
                        lambda *a, **k: _Leaked())
    assert policy_check.main() == 1, "默认关闭失效却没红"
    assert "FAIL" in capsys.readouterr().err


def test_policy_check_flags_when_ceiling_is_not_enforced(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """显式开启后仍要受 max_impact 顶棚约束；不受就必须红。"""
    import policy_check

    class _Pass1:
        blocked = {oid: "default_off" for oid in policy_check.policy.DEFAULT_OFF}

    class _Pass2:
        blocked: dict[str, str] = {}          # 开启后没被顶棚挡

    seq = iter([_Pass1(), _Pass2()])
    monkeypatch.setattr(policy_check.policy, "check_enabled",
                        lambda *a, **k: next(seq))
    assert policy_check.main() == 1, "开启后未受顶棚约束却没红"
    assert "max_impact" in capsys.readouterr().err


def test_policy_check_passes_on_this_repo() -> None:
    import policy_check

    assert policy_check.main() == 0, "当前仓库的默认关闭策略本身失效了"


# ── B436：dead_exports 不能把「同文件在用」当成死代码 ─────────────
def test_dead_exports_counts_same_file_usage(tmp_path: Path,
                                            monkeypatch: pytest.MonkeyPatch) -> None:
    """`tools/surface.py` 里 `"fs.read": FsReadCall` 这种注册表值曾被算成死代码。

    规则原来只扫「别的文件」，于是同文件里的注册与类型注解全被排除——
    89 个「死代码」里大半是活的，**按它删会删掉有用代码**。
    """
    import dead_exports  # type: ignore[import-not-found]

    core = tmp_path / "hos_ares_core"
    core.mkdir(parents=True)
    (core / "reg.py").write_text(
        "class FsReadCall:\n    pass\n\nREGISTRY = {'fs.read': FsReadCall}\n",
        encoding="utf-8",
    )
    (core / "really_dead.py").write_text(
        "class NobodyUsesMe:\n    pass\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(dead_exports, "ROOT", tmp_path)
    monkeypatch.setattr(dead_exports, "CORE", core)
    monkeypatch.setattr(dead_exports, "CLI", tmp_path / "hos_ares_cli")
    monkeypatch.setattr(dead_exports, "TESTS", tmp_path / "tests")

    rows = dead_exports.scan()
    names = {str(r["symbol"]) for r in rows}
    assert "FsReadCall" not in names, "同文件里注册的类被算成死代码"
    assert "NobodyUsesMe" in names, "真死代码反而没报"


# ── B437：框架回调不算死代码 ─────────────────────────────────────
def test_dead_exports_ignores_framework_callbacks(tmp_path: Path,
                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    """`HTMLParser` 的 `handle_starttag` 由基类回调，文本里搜不到名字是正常的。

    上一轮修了「同文件在用」，这轮修「框架回调」——两条加起来，误报从 89 降到 23。
    """
    import dead_exports

    core = tmp_path / "hos_ares_core"
    core.mkdir(parents=True)
    (core / "parser.py").write_text(
        "from html.parser import HTMLParser\n\n\n"
        "class Outline(HTMLParser):\n"
        "    def handle_starttag(self, tag, attrs):\n"
        "        return None\n\n"
        "    def a_real_dead_method(self):\n"
        "        return 1\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(dead_exports, "ROOT", tmp_path)
    monkeypatch.setattr(dead_exports, "CORE", core)
    monkeypatch.setattr(dead_exports, "CLI", tmp_path / "hos_ares_cli")
    monkeypatch.setattr(dead_exports, "TESTS", tmp_path / "tests")

    names = {str(r["symbol"]) for r in dead_exports.scan()}
    assert "handle_starttag" not in names, "框架回调被算成死代码"
    assert "a_real_dead_method" in names, "非回调的私有方法仍应报"
