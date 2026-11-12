"""B316 · manifest 里的哈希必须能用 `sha256sum` 对盘上文件核。

实测（改前）：manifest 用 `sort_keys=True` 算哈希、文件用 `indent=2` 无排序写出，
文本还经过 Windows 的 CRLF 翻译——三个文件的 sha256 **没有一个**对得上。
客户按常规办法验，只会得出"文件被改过"的结论。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from veripen.report.lint import build_manifest, dumps_artifact, verify_manifest


def _payload() -> tuple[str, dict, dict]:
    return "# 报告\n\n- 一行\n", {"version": "2.1.0", "runs": []}, {"findings": []}


def test_file_hashes_match_the_bytes_we_write(tmp_path: Path) -> None:
    md, sarif, js = _payload()
    manifest = build_manifest(markdown=md, sarif=sarif, js=js)
    files = {"report.md": md, "report.sarif": dumps_artifact(sarif),
             "findings.json": dumps_artifact(js)}
    for name, text in files.items():
        path = tmp_path / name
        path.write_text(text, encoding="utf-8", newline=chr(10))
        got = hashlib.sha256(path.read_bytes()).hexdigest()
        assert got == manifest["files"][name], f"{name} 的哈希对不上盘上文件"


def test_verify_manifest_still_catches_a_change() -> None:
    md, sarif, js = _payload()
    manifest = build_manifest(markdown=md, sarif=sarif, js=js)
    ok, problems = verify_manifest(manifest, markdown=md, sarif=sarif, js=js)
    assert ok, problems
    ok2, problems2 = verify_manifest(manifest, markdown=md + "x", sarif=sarif, js=js)
    assert not ok2 and problems2


def test_manifest_is_json_round_trippable() -> None:
    md, sarif, js = _payload()
    manifest = build_manifest(markdown=md, sarif=sarif, js=js)
    again = json.loads(json.dumps(manifest, ensure_ascii=False, indent=2))
    ok, problems = verify_manifest(again, markdown=md, sarif=sarif, js=js)
    assert ok, problems


# ── B317：平台汇总报告的清单也要能对盘上字节核 ──────────────────
def test_platform_manifest_has_a_files_section() -> None:
    import inspect

    from veripen_cli import cmd_platform

    src = inspect.getsource(cmd_platform.main)
    assert '"files": {"platform-report.md": md_sha}' in src, src[-800:]
    assert "newline=chr(10)" in src, "写盘要固定 LF，否则哈希对不上"
