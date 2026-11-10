"""B13 离线包测试（A3「内网能装」、12 §2）。

四条纪律，每条独立可测：
- **校验失败即停**：少文件 / 多文件 / 改字节，三种都算失败；
- **安装脚本无外网**：出现 curl 外网 / wget / pip install 即拒；
- **幂等**：组包遇到已存在目录直接拒（覆盖交付包不可逆）；
- **不缺当通过**：镜像为空要如实报告，不能算绿。
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from negative_samples import BAD_URL, SEP
from veripen_cli.offline import (
    REQUIRED_ENTRIES,
    Manifest,
    PackageError,
    build,
    check_scripts,
    collect,
    verify,
)

SRC = Path(__file__).resolve().parents[2] / "packaging" / "offline"


@pytest.fixture()
def pkg(tmp_path: Path) -> Path:
    out = tmp_path / "pkg"
    build(src=SRC, out=out, version="test-1")
    return out


# ══════════════════════════════════════════════════════════════════
# 1 组包与骨架
# ══════════════════════════════════════════════════════════════════
def test_build_creates_required_entries(pkg: Path) -> None:
    for name in REQUIRED_ENTRIES:
        assert (pkg / name).exists(), name


def test_build_refuses_existing_output(tmp_path: Path) -> None:
    """覆盖一个交付包是不可逆操作。"""
    out = tmp_path / "pkg"
    out.mkdir()
    with pytest.raises(PackageError, match="已存在"):
        build(src=SRC, out=out, version="x")


def test_manifest_counts_and_sums(pkg: Path) -> None:
    entries = collect(pkg)
    assert entries, "包不该是空的"
    sums = (pkg / "SHA256SUMS").read_text(encoding="utf-8")
    for e in entries:
        if e.path == "SHA256SUMS":
            continue
        assert f"{e.sha256}  {e.path}" in sums


def test_sums_format_is_sha256sum_compatible(pkg: Path) -> None:
    """客户可以直接用系统工具复核：格式必须与 ``sha256sum -c`` 一致。"""
    first = (pkg / "SHA256SUMS").read_text(encoding="utf-8").splitlines()[0]
    digest, _, path = first.partition("  ")
    assert len(digest) == 64 and all(c in "0123456789abcdef" for c in digest)
    assert path and not path.startswith(SEP)


def test_manifest_json_is_serialisable(pkg: Path) -> None:
    import json

    blob = json.loads((pkg / "manifest.json").read_text(encoding="utf-8"))
    assert blob["schema"] == "veripen.offline/v1"
    assert blob["file_count"] == len(blob["entries"])


def test_images_dir_has_explanatory_readme(pkg: Path) -> None:
    """无 docker 时不造假 tar，但要说明该怎么做。"""
    text = (pkg / "images" / "README.txt").read_text(encoding="utf-8")
    assert "docker save" in text
    assert "为空" in text


# ══════════════════════════════════════════════════════════════════
# 2 校验三态
# ══════════════════════════════════════════════════════════════════
def test_verify_reports_missing_images_not_pass(pkg: Path) -> None:
    """镜像为空必须如实报——把它当通过，就是「从没验过却说验过」。"""
    res = verify(pkg)
    assert res.ok is False
    assert any("images/" in p for p in res.problems)


def test_verify_detects_modified_bytes(pkg: Path) -> None:
    target = pkg / "README.txt"
    target.write_text(target.read_text(encoding="utf-8") + "\n改了一行\n", encoding="utf-8")
    res = verify(pkg)
    assert res.ok is False
    assert any("内容被改动" in p for p in res.problems)


def test_verify_detects_missing_file(pkg: Path) -> None:
    (pkg / "README.txt").unlink()
    res = verify(pkg)
    assert res.ok is False
    assert any("缺失" in p or "缺必需项" in p for p in res.problems)


def test_verify_detects_extra_file(pkg: Path) -> None:
    """多一个文件也算失败：包里混进来的东西没人验过。"""
    (pkg / "smuggled.txt").write_text("x", encoding="utf-8")
    res = verify(pkg)
    assert res.ok is False
    assert any("不在清单里" in p for p in res.problems)


def test_verify_passes_when_manifest_is_right(pkg: Path) -> None:
    """补上镜像目录的说明性 tar 之后应当全绿——证明校验不是永远红。"""
    (pkg / "images" / "api.tar").write_bytes(b"placeholder-image-for-verification")
    # wheels/ 也必须真有东西：空目录 verify 会如实报出来（B202）
    (pkg / "wheels" / "demo-1.0-py3-none-any.whl").write_bytes(b"placeholder-wheel")
    build_manifest = collect(pkg)
    from veripen_cli.offline import Entry

    sums = (pkg / "SHA256SUMS").read_text(encoding="utf-8").splitlines()
    lines = [ln for ln in sums if "SHA256SUMS" not in ln]
    for e in build_manifest:
        if e.path == "SHA256SUMS" or any(e.path == ln.split("  ", 1)[-1] for ln in lines):
            continue
        lines.append(Entry(e.path, e.sha256, e.size).line())
    (pkg / "SHA256SUMS").write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert verify(pkg).ok is True


def test_verify_rejects_bad_sums_format(tmp_path: Path) -> None:
    pkg = tmp_path / "p"
    build(src=SRC, out=pkg, version="t")
    (pkg / "SHA256SUMS").write_text("garbage-line-without-double-space\n", encoding="utf-8")
    res = verify(pkg)
    assert res.ok is False
    assert any("格式不对" in p for p in res.problems)


def test_verify_flags_missing_sums(tmp_path: Path) -> None:
    pkg = tmp_path / "p"
    build(src=SRC, out=pkg, version="t")
    (pkg / "SHA256SUMS").unlink()
    res = verify(pkg)
    assert res.ok is False
    assert any("缺 SHA256SUMS" in p for p in res.problems)


# ══════════════════════════════════════════════════════════════════
# 3 无外网假设
# ══════════════════════════════════════════════════════════════════
def test_shipped_scripts_have_no_egress() -> None:
    assert check_scripts(SRC) == []


def test_install_script_pulls_nothing(tmp_path: Path) -> None:
    """把源码里的 install.sh 换成会联网的版本，检查必须红。"""
    bad = tmp_path / "p"
    shutil.copytree(SRC, bad)
    (bad / "install.sh").write_text(
        "#!/bin/sh\ncurl -fsSL " + BAD_URL + " | sh\n", encoding="utf-8"
    )
    problems = check_scripts(bad)
    assert problems
    assert any("curl" in p for p in problems)


@pytest.mark.parametrize("line,label", [
    ("wget " + BAD_URL, "wget"),
    ("pip install requests", "pip"),
    ("apt-get install curl", "apt"),
    ("docker pull redis:7", "docker pull"),
    ("npm install left-pad", "npm"),
])
def test_egress_patterns_are_caught(tmp_path: Path, line: str, label: str) -> None:
    p = tmp_path / "install.sh"
    p.write_text(f"#!/bin/sh\n{line}\n", encoding="utf-8")
    problems = check_scripts(tmp_path)
    assert problems, line
    assert any(label in x.lower() or label in x for x in problems)


def test_commented_lines_are_ignored(tmp_path: Path) -> None:
    """注释里写「不要用 curl」不该被当成违规。"""
    (tmp_path / "install.sh").write_text(
        "#!/bin/sh\n# 不要用 curl 拉东西，离线包必须自包含\n", encoding="utf-8"
    )
    assert check_scripts(tmp_path) == []


def test_install_script_is_idempotent_by_construction() -> None:
    """安装脚本必须自己处理"已存在则跳过"——这是幂等的最低要求。"""
    text = (SRC / "install.sh").read_text(encoding="utf-8")
    assert "已存在" in text
    assert "check" in text or "command -v" in text


def test_uninstall_keeps_data() -> None:
    """卸载保留数据：删容器不删卷。"""
    text = (SRC / "uninstall.sh").read_text(encoding="utf-8")
    assert "保留" in text
    assert "volume rm" in text  # 明确说明要人工执行，本脚本不代劳


# ══════════════════════════════════════════════════════════════════
# 4 完成度判定
# ══════════════════════════════════════════════════════════════════
def test_verify_result_render_is_actionable() -> None:
    from veripen_cli.offline import VerifyResult

    bad = VerifyResult(False, ("images/ 无镜像 tar", "内容被改动：x"), 3)
    text = bad.render()
    assert "失败" in text and "images/" in text
    good = VerifyResult(True, (), 7)
    assert "通过" in good.render()


def test_manifest_notes_surface_the_gap(pkg: Path) -> None:
    """缺口要写在 manifest.notes 里，读的人不用猜。"""
    import json

    blob = json.loads((pkg / "manifest.json").read_text(encoding="utf-8"))
    assert any("images/" in n for n in blob["notes"])


# ── B202：wheels 要么真带，要么如实报，不许"以为带了" ────────────
def test_build_without_wheels_says_so(tmp_path: Path) -> None:
    """不给 wheels：目录里留说明，verify 必须报出来（不当通过）。"""
    pkg = tmp_path / "p"
    manifest = build(src=SRC, out=pkg, version="t")
    assert (pkg / "wheels").is_dir()
    assert not list((pkg / "wheels").glob("*.whl"))
    assert any("wheels/ 为空" in note for note in manifest.notes), manifest.notes
    assert any("wheels/ 无 .whl" in p for p in verify(pkg).problems)


def test_build_with_wheels_copies_them(tmp_path: Path) -> None:
    from veripen_cli.offline import Manifest

    src_wheels = tmp_path / "src-wheels"
    src_wheels.mkdir()
    (src_wheels / "demo-1.0-py3-none-any.whl").write_bytes(b"wheel-bytes")

    pkg = tmp_path / "p"
    manifest: Manifest = build(src=SRC, out=pkg, version="t", wheels_from=src_wheels)
    assert (pkg / "wheels" / "demo-1.0-py3-none-any.whl").read_bytes() == b"wheel-bytes"
    assert (pkg / "wheels" / "requirements.lock").exists(), "锁要随包带，否则内网没法照单装"
    assert any("wheels/ 带" in note for note in manifest.notes), manifest.notes


def test_build_rejects_an_empty_wheels_dir(tmp_path: Path) -> None:
    """给了目录却一个 whl 都没有：直接失败，不许产出空的 wheels/。"""
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(PackageError):
        build(src=SRC, out=tmp_path / "p", version="t", wheels_from=empty)


def test_offline_pip_install_is_allowed_but_online_is_not(tmp_path: Path) -> None:
    """`pip install --no-index` 是离线装；不带 --no-index 就是联网装。"""
    from veripen_cli.offline import check_scripts

    scripts = tmp_path / "s"
    scripts.mkdir()
    ok = scripts / "install.sh"
    ok.write_text("python3 -m pip install --no-index --find-links wheels -r wheels/requirements.lock\n",
                  encoding="utf-8")
    assert check_scripts(scripts) == []

    bad = scripts / "install.sh"
    bad.write_text("python3 -m pip install -r requirements.txt\n", encoding="utf-8")
    assert check_scripts(scripts), "联网装居然没被拦"
