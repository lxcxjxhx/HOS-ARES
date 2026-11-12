"""B247 · 交付物里两处"看起来坏了"的渲染。

① `veripen run` 的报告头部，profile_hash / image 在无客户档时是空的，旧版渲染成
   两个**空反引号**：`- profile_hash: `` · image: ``` —— 读者会以为渲染坏了。
② `veripen platform` 的"路由来源"原样打出 `--openapi` 参数，于是交付物里出现
   `C:\\…\\out\\platform-check\\spec.json` 这种本机绝对路径：带出操作员的目录结构，
   别人拿到报告也照抄不了。
"""

from __future__ import annotations

from pathlib import Path

from veripen.report import FindingView, ReportInput, render_markdown
from veripen_cli.cmd_platform import _portable_path


def _rep(**kw) -> ReportInput:  # noqa: ANN003
    base = dict(
        engagement="ENG-1", profile_hash="", image_digest="",
        root_sha256="a" * 64, routeset_sha256=["b" * 64], resolver_version="1.0.0",
        findings=(FindingView(
            finding_id="f-1", title="对象级授权缺失", asset_id="web-01",
            oracle_id="idor_cross_account", cwe="CWE-639", severity="high",
            confidence=0.95, route_strength="contract",
            route_ref="route://contract/7f3a2c11d9e04b58",
            capsule_id="c" * 64, self_sha256="c" * 64, replay_status="OK",
        ),),
        coverage={"zero_hit_tags": []}, frameworks=[],
    )
    base.update(kw)
    return ReportInput(**base)


def test_absent_profile_and_image_say_weibu_tigong() -> None:
    text = render_markdown(_rep())
    assert "- profile_hash: 未提供 · image: 未提供" in text, text[:300]
    assert "``" not in text.split("\n\n")[0], "头部不许出现空反引号"


def test_present_profile_and_image_stay_short_hashes() -> None:
    text = render_markdown(_rep(profile_hash="h" * 64, image_digest="d" * 64))
    assert "- profile_hash: `" + "h" * 16 + "`" in text
    assert "image: `" + "d" * 16 + "`" in text


def test_portable_path_relativizes_inside_cwd(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.chdir(tmp_path)
    spec = tmp_path / "out" / "spec.json"
    spec.parent.mkdir(parents=True, exist_ok=True)
    spec.write_text("{}", encoding="utf-8")
    assert _portable_path(str(spec)) == "out/spec.json", "在当前目录下要给相对路径"


def test_portable_path_falls_back_to_the_filename(tmp_path) -> None:  # noqa: ANN001
    outside = tmp_path / "elsewhere" / "spec.json"
    outside.parent.mkdir(parents=True, exist_ok=True)
    outside.write_text("{}", encoding="utf-8")
    shown = _portable_path(str(outside))
    assert shown == "spec.json", f"目录之外的路径只留文件名：{shown}"
    assert str(Path.cwd()) not in shown


def test_portable_path_handles_empty() -> None:
    assert _portable_path("") == "（未指定）"
