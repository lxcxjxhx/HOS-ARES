"""B58 交付双形态（`H02 §3` / `H07` B58）。

判据：light 包**不含**浏览器载荷 · browser 包含但**不假装已装 Chromium** ·
manifest 记录 profile · 非法形态直接拒 · doctor 分别报 sandbox / browser 可用性。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from veripen_cli.offline import PROFILES, PackageError, build, verify

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "packaging" / "offline"


def _build(tmp_path: Path, profile: str) -> Path:
    out = tmp_path / f"pkg-{profile}"
    build(src=SRC, out=out, version="9.9.9", profile=profile)
    return out


def test_light_package_has_no_browser_payload(tmp_path: Path) -> None:
    out = _build(tmp_path, "light")
    assert not (out / "browser").exists()
    names = " ".join(p.relative_to(out).as_posix().lower() for p in out.rglob("*"))
    assert "chromium" not in names and "playwright" not in names


def test_browser_profile_declares_but_does_not_fake_chromium(tmp_path: Path) -> None:
    out = _build(tmp_path, "browser")
    assert (out / "browser").is_dir()
    entries = list((out / "browser").iterdir())
    assert [p.name for p in entries] == ["README.txt"], "只有说明，不许塞占位二进制"
    assert "Chromium" in (out / "browser" / "README.txt").read_text(encoding="utf-8")


def test_manifest_records_profile(tmp_path: Path) -> None:
    import json

    for profile in PROFILES:
        out = _build(tmp_path, profile)
        manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["profile"] == profile


def test_unknown_profile_is_refused(tmp_path: Path) -> None:
    with pytest.raises(PackageError) as err:
        build(src=SRC, out=tmp_path / "bad", version="1", profile="huge")
    assert "未知包形态" in str(err.value)


@pytest.mark.parametrize("profile", PROFILES)
def test_both_profiles_verify_and_report_the_known_gap(tmp_path: Path, profile: str) -> None:
    """两种形态都能组包；verify 必须**如实报出** images/ 为空（A3 的 docker 缺口）。"""
    out = _build(tmp_path, profile)
    result = verify(out)
    joined = " ".join(result.problems)
    assert "images" in joined, "无 docker 构建必须被 verify 报出来，不许静默通过"
    assert not result.ok


def test_doctor_reports_sandbox_and_browser_separately() -> None:
    from veripen.doctor import run_all

    detail = {c.name: c.detail for c in run_all().checks}["deps"]
    assert "sandbox: " in detail and "browser: " in detail
    assert "tui: " in detail, "三项能力必须都在这条诊断里"
