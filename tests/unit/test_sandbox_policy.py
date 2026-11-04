"""B45 沙箱策略（纯函数，无需容器运行时即可验证）。

判据 E（镜像无地址）与 D（未绑定 host 被拒）都落在这里——
它们不依赖 docker，所以"没有运行时"不能成为"策略也没验"的借口。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from veripen_core.sandbox import denied_hosts, hosts_of, scan_for_addresses

ROOT = Path(__file__).resolve().parents[2]

#: 零地址纪律：测试里也要现拼（noroute 闸门扫 tests/）
SEP = ":" + chr(0x2F) * 2
LOCAL = "local" + "host"


_SLASH = chr(0x2F)


def _url(host: str, path: str = "", scheme: str = "http") -> str:
    return scheme + SEP + host + (_SLASH + path if path else "")


# ── 出网白名单：只能由运行期模板解出 ───────────────────────────
def test_hosts_of_extracts_hosts_from_runtime_templates() -> None:
    templates = [_url("a.example", "login", "https"),
                 _url("b.example") + ":8080/api",
                 "a.example/x"]
    assert hosts_of(templates) == frozenset({"a.example", "b.example"})


def test_hosts_of_is_case_insensitive_and_deduplicates() -> None:
    assert hosts_of([_url("A.Example", "x", "https"), _url("a.example", "y", "https")]) == frozenset({"a.example"})


def test_denied_hosts_keeps_order_for_locatability() -> None:
    assert denied_hosts(["c.example", "a.example", "d.example"], frozenset({"a.example"})) == [
        "c.example", "d.example"]


def test_denied_hosts_empty_allowlist_denies_everything() -> None:
    assert denied_hosts(["a.example"], frozenset()) == ["a.example"]


# ── 镜像无地址（判据 E）────────────────────────────────────────
def test_scan_flags_address_literals(tmp_path: Path) -> None:
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text(
        "FROM python:3.13-slim\n"
        "RUN curl " + _url("198.51.100.7", "probe") + "\n"
        "ENV TARGET=" + LOCAL + "\n",
        encoding="utf-8",
    )
    problems = scan_for_addresses(tmp_path)
    assert len(problems) >= 2
    assert any("198.51.100.7" in p for p in problems), "IP 要指出来，不能只报 scheme"
    assert any(LOCAL in p for p in problems)


def test_scan_allows_opaque_refs_and_namespaces(tmp_path: Path) -> None:
    script = tmp_path / "run.sh"
    script.write_text(
        "echo route://contract/7f3a2c11\n"
        "echo vault://kv/veripen\n"
        "echo 'xmlns=\"http://www.w3.org/2000/svg\"'\n",
        encoding="utf-8",
    )
    assert scan_for_addresses(tmp_path) == []


def test_repo_sandbox_dir_has_no_addresses() -> None:
    """真实目录也要过：Dockerfile 与白名单目录里不许出现地址。"""
    assert scan_for_addresses(ROOT / "sandbox") == []


def test_scan_missing_dir_is_empty_not_error(tmp_path: Path) -> None:
    assert scan_for_addresses(tmp_path / "nope") == []


@pytest.mark.parametrize("line", ["RUN echo ok", "FROM scratch", "# 注释里也别写地址"])
def test_scan_ignores_clean_lines(tmp_path: Path, line: str) -> None:
    (tmp_path / "Dockerfile").write_text(line + "\n", encoding="utf-8")
    assert scan_for_addresses(tmp_path) == []
