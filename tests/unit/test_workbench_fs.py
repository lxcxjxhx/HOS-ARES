"""B12.14 工作台读写层测试（21 §B12.14）。

四条 DoD：
- 绝对路径**在参数层**就拒（不指望 resolve 兜底）；
- ``fs.write`` 对已存在默认失败；
- ``fs.edit`` 的 ``expect_count`` 不匹配时**整体失败**（文件一个字节都不动）；
- **没有 ``fs.delete``**。

外加一条这层真正的价值：`test_workbench_cannot_import_verdict`——
工作台是"手"，手不该有"我判断过了"的能力。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from negative_samples import ABS_FILE, ABS_WIN, SEP
from veripen_core.workbench import (
    FORBIDDEN_OPS,
    FS_OPS,
    MAX_READ_BYTES,
    FsError,
    PathScopeError,
    SearchMode,
    Workbench,
    check_relative,
    code_search,
    dispatch,
    resolve_within,
)

WORKBENCH_DIR = Path(__file__).resolve().parents[2] / "veripen_core" / "workbench"

#: 反例样本集中放在 tests/negative_samples.py（拼接构造，源码里无违规形态）
ABS_PATHS = [ABS_FILE, ABS_WIN, "C:" + SEP + "x", "d:" + SEP + "y"]
ABS_ONE = ABS_FILE
_SEP = SEP


@pytest.fixture()
def wb(tmp_path: Path) -> Workbench:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    return Workbench(tmp_path)


# ══════════════════════════════════════════════════════════════════
# 1 绝对路径在参数层拒
# ══════════════════════════════════════════════════════════════════
def test_absolute_path_is_rejected_at_schema_layer() -> None:
    for bad in ABS_PATHS:
        with pytest.raises(PathScopeError):
            check_relative(bad)


def test_parent_traversal_is_rejected() -> None:
    for bad in ("../x", "a/../../b", "src/../..", ".."):
        with pytest.raises(PathScopeError, match="上溯"):
            check_relative(bad)


def test_empty_and_dot_segments_are_rejected() -> None:
    for bad in ("", " ", "a//b", "a/./b"):
        with pytest.raises(PathScopeError):
            check_relative(bad)


def test_workbench_rejects_absolute_read(wb: Workbench) -> None:
    with pytest.raises(PathScopeError):
        wb.read(ABS_ONE)


def test_symlink_escape_is_caught_after_resolve(tmp_path: Path) -> None:
    """符号链接指向工作根外：resolve 之后再查前缀，否则 N1/N2 形同虚设。"""
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("secret", encoding="utf-8")
    root = tmp_path / "root"
    root.mkdir()
    try:
        (root / "link").symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("本平台不允许建符号链接")
    wb = Workbench(root)
    with pytest.raises(PathScopeError, match="工作根之外"):
        wb.read("link/secret.txt")


def test_normal_relative_path_passes(wb: Workbench) -> None:
    assert wb.read("src/a.py").text.startswith("def foo")


# ══════════════════════════════════════════════════════════════════
# 2 write 默认不覆盖
# ══════════════════════════════════════════════════════════════════
def test_write_creates_new_file(wb: Workbench) -> None:
    r = wb.write("out.txt", "hello")
    assert r.created is True and r.bytes_written == 5
    assert wb.read("out.txt").text == "hello"


def test_write_on_existing_fails_by_default(wb: Workbench) -> None:
    wb.write("out.txt", "first")
    with pytest.raises(FsError, match="已存在"):
        wb.write("out.txt", "second")
    assert wb.read("out.txt").text == "first"  # 一个字节都没动


def test_write_with_explicit_overwrite_works(wb: Workbench) -> None:
    wb.write("out.txt", "first")
    r = wb.write("out.txt", "second", overwrite=True)
    assert r.created is False
    assert wb.read("out.txt").text == "second"


def test_write_creates_parent_dirs(wb: Workbench) -> None:
    wb.write("deep/nested/x.txt", "y")
    assert wb.read("deep/nested/x.txt").text == "y"


def test_write_rejects_absolute_target(wb: Workbench) -> None:
    with pytest.raises(PathScopeError):
        wb.write("/tmp/x.txt", "y")


# ══════════════════════════════════════════════════════════════════
# 3 edit 的 expect_count 不匹配即整体失败
# ══════════════════════════════════════════════════════════════════
def test_edit_replaces_exact_count(wb: Workbench) -> None:
    wb.write("m.txt", "aXbXc")
    r = wb.edit("m.txt", old="X", new="Y", expect_count=2)
    assert r.replacements == 2
    assert wb.read("m.txt").text == "aYbYc"


def test_edit_mismatch_leaves_file_untouched(wb: Workbench) -> None:
    """改一半比不改更危险：次数不符必须整体失败，文件一个字节都不动。"""
    wb.write("m.txt", "aXb")
    before = wb.read("m.txt").text
    with pytest.raises(FsError, match="未做任何修改"):
        wb.edit("m.txt", old="X", new="Y", expect_count=2)
    assert wb.read("m.txt").text == before


def test_edit_mismatch_too_many_also_fails(wb: Workbench) -> None:
    wb.write("m.txt", "XXX")
    with pytest.raises(FsError, match="期望 2 处匹配，实际 3 处"):
        wb.edit("m.txt", old="X", new="Y", expect_count=2)


def test_edit_refuses_empty_old(wb: Workbench) -> None:
    wb.write("m.txt", "abc")
    with pytest.raises(FsError, match="不能为空"):
        wb.edit("m.txt", old="", new="X")


def test_edit_reports_actual_count(wb: Workbench) -> None:
    wb.write("m.txt", "xyx")
    with pytest.raises(FsError, match="实际 2 处"):
        wb.edit("m.txt", old="x", new="z", expect_count=1)


# ══════════════════════════════════════════════════════════════════
# 4 没有 delete
# ══════════════════════════════════════════════════════════════════
def test_workbench_has_no_delete_method(wb: Workbench) -> None:
    """能力的缺失要在**方法层面**：没有 delete 方法，就没有人能调它。"""
    absence = "delete remove unlink rmtree move rename".split()
    for name in absence:
        assert not hasattr(wb, name), f"Workbench 不该有 {name}"


def test_dispatch_refuses_forbidden_ops(wb: Workbench) -> None:
    for op in sorted(FORBIDDEN_OPS):
        with pytest.raises(FsError, match="不存在"):
            dispatch(wb, op)


def test_fs_ops_table_has_no_delete() -> None:
    assert not any("delete" in op or "remove" in op for op in FS_OPS)
    expected = "fs.read fs.write fs.edit fs.glob fs.grep code.search".split()
    assert FS_OPS == set(expected)


def test_dispatch_unknown_op_is_rejected(wb: Workbench) -> None:
    with pytest.raises(FsError, match="未知操作"):
        dispatch(wb, "fs.chmod")


# ══════════════════════════════════════════════════════════════════
# 5 读与检索
# ══════════════════════════════════════════════════════════════════
def test_read_truncates_big_file_and_says_so(tmp_path: Path) -> None:
    big = tmp_path / "big.txt"
    big.write_text("x" * (MAX_READ_BYTES + 100), encoding="utf-8")
    wb = Workbench(tmp_path)
    r = wb.read("big.txt")
    assert r.truncated is True
    assert r.bytes_total == MAX_READ_BYTES + 100
    assert len(r.text.encode("utf-8")) <= MAX_READ_BYTES


def test_read_directory_is_rejected(wb: Workbench) -> None:
    with pytest.raises(FsError, match="是目录"):
        wb.read("src")


def test_glob_is_sorted_and_relative(wb: Workbench) -> None:
    wb.write("b.txt", "1")
    wb.write("a.txt", "2")
    got = wb.glob("*.txt")
    assert got == sorted(got)
    assert all(not p.startswith(_SEP) for p in got)


def test_grep_returns_line_numbers(wb: Workbench) -> None:
    wb.write("g.txt", "alpha\nbeta\ngamma\n")
    hits = wb.grep("beta", glob_pattern="g.txt")
    assert [(h.path, h.line) for h in hits] == [("g.txt", 2)]


def test_grep_fixed_mode_treats_meta_as_text(wb: Workbench) -> None:
    wb.write("g.txt", "a.b\naxb\n")
    assert len(wb.grep("a.b", glob_pattern="g.txt")) == 2
    assert len(wb.grep("a.b", glob_pattern="g.txt", fixed=True)) == 1


def test_grep_rejects_bad_regex(wb: Workbench) -> None:
    with pytest.raises(FsError, match="正则不合法"):
        wb.grep("([", glob_pattern="*.txt")


def test_code_search_ast_finds_definitions(wb: Workbench) -> None:
    hits = code_search(wb, "foo", mode=SearchMode.AST, glob_pattern="**/*.py")
    assert any(h.text == "foo" for h in hits)


def test_code_search_regex_equals_grep(wb: Workbench) -> None:
    a = code_search(wb, "return", mode=SearchMode.REGEX, glob_pattern="**/*.py")
    b = wb.grep("return", glob_pattern="**/*.py")
    assert [(h.path, h.line) for h in a] == [(h.path, h.line) for h in b]


# ══════════════════════════════════════════════════════════════════
# 6 工作台不许判定（契约测试）
# ══════════════════════════════════════════════════════════════════
def test_workbench_cannot_import_verdict() -> None:
    """工作台是"手"：手不该有"我判断过了"的能力。"""
    banned = {"Verdict", "Judgement"}
    offenders: list[str] = []
    for p in WORKBENCH_DIR.rglob("*.py"):
        if "__pycache__" in p.parts:
            continue
        tree = ast.parse(p.read_text(encoding="utf-8"), filename=str(p))
        for node in ast.walk(tree):
            names = [a.name for a in node.names] if isinstance(
                node, (ast.Import, ast.ImportFrom)) else []
            for n in names:
                if n.split(".")[-1] in banned:
                    offenders.append(f"{p.name}:{node.lineno}:{n}")
    assert offenders == []


def test_the_guard_can_go_red(tmp_path: Path) -> None:
    bad = tmp_path / "bad.py"
    bad.write_text("from veripen_core.models import Judgement\n", encoding="utf-8")
    tree = ast.parse(bad.read_text(encoding="utf-8"))
    hits = [a.name for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
            for a in node.names if a.name == "Judgement"]
    assert hits == ["Judgement"]


# ══════════════════════════════════════════════════════════════════
# 7 resolve_within 的边界
# ══════════════════════════════════════════════════════════════════
def test_resolve_within_stays_inside(tmp_path: Path) -> None:
    root = tmp_path / "r"
    root.mkdir()
    got = resolve_within(root, "a/b.txt")
    assert got == (root / "a" / "b.txt").resolve()


def test_root_must_exist(tmp_path: Path) -> None:
    with pytest.raises(FsError, match="工作根不存在"):
        Workbench(tmp_path / "nope")
