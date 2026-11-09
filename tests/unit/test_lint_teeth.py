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
    import noroute

    # 闸门只扫产品目录（含 web）：样本要放在里面
    bad = tmp_path / "veripen_core" / "bad.py"
    bad.parent.mkdir(parents=True, exist_ok=True)
    bad.write_text('TARGET = "http' + ":" + SEP + SEP + 'example.invalid"\n',
                   encoding="utf-8")
    hits = noroute._scan(tmp_path)
    assert hits, "把地址写进代码居然没命中——这道闸门没牙"


def test_noroute_passes_a_clean_file(tmp_path: Path) -> None:
    import noroute

    good = tmp_path / "veripen_core" / "good.py"
    good.parent.mkdir(parents=True, exist_ok=True)
    good.write_text('SEP = chr(0x2F)\nPATH = "web" + SEP + "static"\n', encoding="utf-8")
    assert noroute._scan(tmp_path) == [], "干净样本不该被误伤"


def test_nodata_flags_a_compound_name_in_product_code(tmp_path: Path,
                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    """`FakeTransport` / `fake_runner` 是真实命名形态，必须抓住（B194）。"""
    import nodata_stub

    root = tmp_path
    (root / "veripen_core").mkdir(parents=True)
    bad = root / "veripen_core" / "bad.py"
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

    (tmp_path / "veripen_core").mkdir(parents=True)
    good = tmp_path / "veripen_core" / "good.py"
    good.write_text("def record_runner():\n    return 1\n", encoding="utf-8")
    monkeypatch.setattr(nodata_stub, "ROOT", tmp_path)
    assert nodata_stub._check_banned([good]) == []


def test_netimports_flags_a_network_library(tmp_path: Path) -> None:
    import netimports

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
    import spec_drift

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
    assert spec_drift.evidence_problem("veripen_core/store.py::DDL:blackboard_facts") == ""


# ── B196：lint-ui 也要能红 ─────────────────────────────────────
def _ui_root(tmp_path: Path) -> Path:
    web = tmp_path / "web"
    (web / "static").mkdir(parents=True)
    return web


def test_ui_zero_address_flags_an_address(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                          capsys: pytest.CaptureFixture[str]) -> None:
    import ui_zero_address

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
