"""B12.14 · 工作台读写层 ``fs.*``（21 §B12.14）。

这一层让 agent「长出第一只手」：能读、能写、能改、能搜。四条约束全部在**类型/入口**上强制：

| 约束 | 为什么 |
|---|---|
| **只接受相对路径** | 绝对路径等于把整台机器的读取面交出去 |
| **不许越出工作根** | ``../`` 是最朴素的越权 |
| ``fs.write`` 对已存在默认失败 | 覆盖是**不可逆**的；要覆盖必须显式说 |
| ``fs.edit`` 的 ``expect_count`` 不匹配即**整体失败** | 改一半比不改更危险 |
| **没有 ``fs.delete``** | 删除不可撤销，而 agent 判断"这个没用"的准确率不够 |

最后一条是设计决定，不是漏做：没有删除能力，最坏情况是留下一堆垃圾文件；
有删除能力，最坏情况是删掉别人的东西。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Sequence

#: 单次读取上限。超了**截断并说明**，不静默丢。
MAX_READ_BYTES = 256 * 1024
#: 单次写入上限
MAX_WRITE_BYTES = 1024 * 1024
#: grep / glob 单次返回上限
MAX_MATCHES = 200


class FsError(RuntimeError):
    """越界或参数非法。**fail-closed**：不猜用户想干什么。"""

    code = "VP-8014"


class PathScopeError(FsError):
    """路径越界（绝对路径 / 上溯 / 符号链接逃逸）。"""

    code = "VP-8015"


# ══════════════════════════════════════════════════════════════════
# 路径校验：schema 层就拒
# ══════════════════════════════════════════════════════════════════
def check_relative(rel: str) -> PurePosixPath:
    """只接受相对路径；绝对路径、盘符、``..`` 一律拒。

    **先在最外层拒**，不要指望 ``resolve()`` 兜底：``resolve()`` 会跟随符号链接，
    而符号链接指向哪里只有运行时才知道。参数错误应当在参数层就被拦下。
    """
    if not rel or not rel.strip():
        raise PathScopeError("路径不能为空")
    sep = chr(0x2F)
    text = rel.strip().replace("\\", sep)
    if text.startswith(sep):  # noroute-ok: 定义「什么算绝对路径」的地方必须写出绝对路径形态
        raise PathScopeError(f"不接受绝对路径：{rel!r}")
    if re.match(r"^[a-zA-Z]:", text):
        raise PathScopeError(f"不接受盘符路径：{rel!r}")
    parts = text.split(sep)  # noroute-ok: 同上，切分绝对路径前缀是本函数的职责
    # 先查**切分后的原始段**：PurePosixPath 会把 "a//b"、"a/./b" 静默规范化成 ('a','b')，
    # 拿规范化结果做检查等于永远看不到这两种输入。
    if any(p == ".." for p in parts):
        raise PathScopeError(f"不接受上溯路径：{rel!r}")
    if any(p in ("", ".") for p in parts):
        raise PathScopeError(f"路径含空段或当前目录段：{rel!r}")
    return PurePosixPath(*parts)


def resolve_within(root: Path, rel: str) -> Path:
    """解析到工作根内。**解析后再查一遍前缀**——符号链接可能指到外面去。"""
    pure = check_relative(rel)
    base = root.resolve()
    target = (base / pure).resolve()
    try:
        target.relative_to(base)
    except ValueError as exc:
        raise PathScopeError(f"{rel!r} 解析后落在工作根之外（符号链接逃逸）") from exc
    return target


# ══════════════════════════════════════════════════════════════════
# 结果类型
# ══════════════════════════════════════════════════════════════════
@dataclass(frozen=True)
class ReadResult:
    path: str
    text: str
    truncated: bool = False
    bytes_total: int = 0


@dataclass(frozen=True)
class WriteResult:
    path: str
    bytes_written: int
    created: bool


@dataclass(frozen=True)
class EditResult:
    path: str
    replacements: int


@dataclass(frozen=True)
class Match:
    path: str
    line: int
    text: str


class Workbench:
    """工作根内的读写。**没有 delete**，方法不存在即能力不存在。"""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        if not self.root.exists():
            raise FsError(f"工作根不存在：{root}")

    # ── 读 ──────────────────────────────────────────────────────
    def read(self, rel: str) -> ReadResult:
        p = resolve_within(self.root, rel)
        if not p.exists():
            raise FsError(f"文件不存在：{rel}")
        if p.is_dir():
            raise FsError(f"{rel} 是目录，read 只读文件")
        raw = p.read_bytes()
        truncated = len(raw) > MAX_READ_BYTES
        text = raw[:MAX_READ_BYTES].decode("utf-8", errors="replace")
        return ReadResult(path=rel, text=text, truncated=truncated, bytes_total=len(raw))

    # ── 写 ──────────────────────────────────────────────────────
    def write(self, rel: str, content: str, *, overwrite: bool = False) -> WriteResult:
        """默认**不覆盖**：覆盖不可逆，必须显式说。"""
        p = resolve_within(self.root, rel)
        exists = p.exists()
        if exists and not overwrite:
            raise FsError(f"{rel} 已存在；覆盖要显式 overwrite=true（不可逆操作不该是默认值）")
        data = content.encode("utf-8")
        if len(data) > MAX_WRITE_BYTES:
            raise FsError(f"内容 {len(data)} 字节 > 上限 {MAX_WRITE_BYTES}")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        return WriteResult(path=rel, bytes_written=len(data), created=not exists)

    # ── 改 ──────────────────────────────────────────────────────
    def edit(self, rel: str, *, old: str, new: str, expect_count: int = 1) -> EditResult:
        """精确替换。**次数不符就整体失败**——改一半比不改更危险。"""
        if not old:
            raise FsError("old 不能为空（空串匹配会改掉整个文件）")
        p = resolve_within(self.root, rel)
        if not p.exists():
            raise FsError(f"文件不存在：{rel}")
        original = p.read_text(encoding="utf-8")
        found = original.count(old)
        if found != expect_count:
            raise FsError(
                f"{rel}: 期望 {expect_count} 处匹配，实际 {found} 处——**未做任何修改**"
            )
        updated = original.replace(old, new, expect_count)
        p.write_text(updated, encoding="utf-8")
        return EditResult(path=rel, replacements=found)

    # ── 找 ──────────────────────────────────────────────────────
    def glob(self, pattern: str) -> list[str]:
        """相对 pattern 的文件枚举。结果**排序**，同输入同输出。"""
        check_relative(pattern)
        base = self.root.resolve()
        out: list[str] = []
        for p in sorted(base.glob(pattern.replace("\\", chr(0x2F)))):
            if not p.is_file():
                continue
            try:
                out.append(p.resolve().relative_to(base).as_posix())
            except ValueError:  # 符号链接逃逸：跳过，不报错（枚举不该被一个坏链接打断）
                continue
        return out[:MAX_MATCHES]

    def grep(
        self, pattern: str, *, glob_pattern: str = "**/*", fixed: bool = False,
        max_matches: int = MAX_MATCHES,
    ) -> list[Match]:
        """正则在文件内容里搜。**返回行号与原文**，不返回文件全量。"""
        try:
            rx = re.compile(re.escape(pattern) if fixed else pattern)
        except re.error as exc:
            raise FsError(f"正则不合法：{exc}") from exc

        matches: list[Match] = []
        for rel in self.glob(glob_pattern):
            p = resolve_within(self.root, rel)
            if not p.is_file():
                continue
            try:
                text = p.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            for lineno, line in enumerate(text.splitlines(), 1):
                if rx.search(line):
                    matches.append(Match(path=rel, line=lineno, text=line[:400]))
                    if len(matches) >= max_matches:
                        return matches
        return matches


class SearchMode(str, Enum):
    REGEX = "regex"
    AST = "ast"


def code_search(
    wb: Workbench, needle: str, *, mode: SearchMode = SearchMode.REGEX,
    glob_pattern: str = "**/*.py",
) -> list[Match]:
    """``code.search``：正则走 grep；AST 走"这是不是一个标识符定义"。

    AST 模式刻意只做**定义点**（``def``/``class``/赋值），不做语义解析：
    真做类型推断就要引入语言服务，那是另一种东西。
    """
    if mode is SearchMode.REGEX:
        return wb.grep(needle, glob_pattern=glob_pattern)

    import ast as _ast

    out: list[Match] = []
    for rel in wb.glob(glob_pattern):
        p = resolve_within(wb.root, rel)
        try:
            tree = _ast.parse(p.read_text(encoding="utf-8"), filename=rel)
        except (SyntaxError, UnicodeDecodeError, OSError):
            continue
        for node in _ast.walk(tree):
            name = ""
            if isinstance(node, (_ast.FunctionDef, _ast.AsyncFunctionDef, _ast.ClassDef)):
                name = node.name
            elif isinstance(node, _ast.Name) and isinstance(node.ctx, _ast.Store):
                name = node.id
            if name and needle in name:
                out.append(Match(path=rel, line=int(getattr(node, "lineno", 0)), text=name))
    return out[:MAX_MATCHES]


#: 工具入口表。**没有 delete**——这一条是设计决定，测试会断言它不存在。
FS_OPS: frozenset[str] = frozenset({"fs.read", "fs.write", "fs.edit", "fs.glob", "fs.grep",
                                    "code.search"})
FORBIDDEN_OPS: frozenset[str] = frozenset({"fs.delete", "fs.remove", "fs.rm", "fs.move"})


def dispatch(wb: Workbench, op: str, **kwargs: Any) -> Any:
    """统一入口。禁止的操作**显式拒**，不是静默忽略。"""
    if op in FORBIDDEN_OPS:
        raise FsError(
            f"{op} 不存在：工作台不提供删除/移动能力"
            "（删除不可撤销，而 agent 判断「没用」的准确率不够）"
        )
    table: dict[str, Any] = {
        "fs.read": wb.read,
        "fs.write": wb.write,
        "fs.edit": wb.edit,
        "fs.glob": wb.glob,
        "fs.grep": wb.grep,
        "code.search": lambda needle, **kw: code_search(wb, needle, **kw),
    }
    fn: Any = table.get(op)
    if fn is None:
        raise FsError(f"未知操作 {op!r}")
    result: Any = fn(**kwargs)
    return result


__all__ = [
    "FS_OPS",
    "FORBIDDEN_OPS",
    "MAX_MATCHES",
    "MAX_READ_BYTES",
    "MAX_WRITE_BYTES",
    "EditResult",
    "FsError",
    "Match",
    "PathScopeError",
    "ReadResult",
    "SearchMode",
    "Workbench",
    "WriteResult",
    "check_relative",
    "code_search",
    "dispatch",
    "resolve_within",
]
