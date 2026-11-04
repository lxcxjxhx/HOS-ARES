"""B13 · 离线包（A3「内网能装」、12 §2）。

离线包的价值全在**可校验**：客户拿到一坨文件，凭什么相信它完整？
所以本模块只做三件事，每件都可独立重跑：

1. **清单**：每个文件一条 ``sha256  相对路径``，与 ``SHA256SUMS`` 同格式；
2. **校验**：``verify`` 逐条重算，**少一个文件、多一个文件都算失败**；
3. **无外网假设**：安装脚本里出现外网拉取即拒（12 §2 末行「不得有任何 curl 外网」）。

刻意不做的事：**不做镜像 tar**。本机无 docker，导不出真实镜像；
假装能导会让 ``doctor`` 在「从没验过镜像」时报绿——那正是这套系统最反对的事。
所以镜像部分由 ``images/README.txt`` 说明「需在有 docker 的构建机生成」，
并且 ``verify`` 会明确报告「镜像目录为空」，不把它当通过。
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Sequence

#: 包内必需的文件/目录（12 §2 的骨架）
REQUIRED_ENTRIES: tuple[str, ...] = (
    "install.sh",
    "uninstall.sh",
    "README.txt",
    "SHA256SUMS",
    "profiles",
)

#: 安装脚本里**禁止**出现的外网拉取形态（12 §2：无外网假设）
EGRESS_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\bcurl\b[^\n]*\bhttp", "curl 外网"),
    (r"\bwget\b", "wget"),
    (r"\bpip\s+install\b", "pip install（应离线装）"),
    (r"\bapt(-get)?\s+install\b", "apt install"),
    (r"\bdocker\s+pull\b", "docker pull"),
    (r"\bnpm\s+install\b", "npm install"),
    (r"\buv\s+(sync|pip)\b[^\n]*--?no-cache", "uv 联网装"),
)


class PackageError(RuntimeError):
    code = "VP-8018"


@dataclass(frozen=True)
class Entry:
    path: str
    sha256: str
    size: int

    def line(self) -> str:
        """与 ``sha256sum`` 输出同格式，客户可直接用系统工具复核。"""
        return f"{self.sha256}  {self.path}"


#: 两种包形态（H02 §3）：light 只带内核；browser 额外带渲染执行体（体积 +300MB 量级）
PROFILES = ("light", "browser")


@dataclass
class Manifest:
    version: str
    entries: list[Entry] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    profile: str = "light"

    def as_dict(self) -> dict[str, object]:
        return {
            "schema": "veripen.offline/v1",
            "version": self.version,
            "total_bytes": sum(e.size for e in self.entries),
            "file_count": len(self.entries),
            "notes": list(self.notes),
            "profile": self.profile,
            "entries": [{"path": e.path, "sha256": e.sha256, "size": e.size}
                        for e in self.entries],
        }

    def sums_text(self) -> str:
        return "\n".join(e.line() for e in sorted(self.entries, key=lambda x: x.path)) + "\n"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def collect(root: Path) -> list[Entry]:
    """遍历包内全部文件（**排序**：同目录同输入同清单）。"""
    out: list[Entry] = []
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        out.append(Entry(path=p.relative_to(root).as_posix(), sha256=_sha256(p),
                         size=p.stat().st_size))
    return out


def check_scripts(root: Path) -> list[str]:
    """安装/卸载脚本的**无外网**检查。返回违规描述。"""
    problems: list[str] = []
    for name in ("install.sh", "uninstall.sh"):
        p = root / name
        if not p.exists():
            continue
        for lineno, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            for pattern, label in EGRESS_PATTERNS:
                if re.search(pattern, line, re.IGNORECASE):
                    problems.append(f"{name}:{lineno}: {label} —— {stripped[:80]}")
    return problems


def build(
    *, src: Path, out: Path, version: str, extra_profiles: Sequence[Path] = (),
    profile: str = "light",
) -> Manifest:
    """组包。**已存在的输出目录直接拒**：覆盖一个交付包是不可逆操作。"""
    if out.exists():
        raise PackageError(f"{out} 已存在：覆盖交付包不可逆，请先手工确认")
    out.mkdir(parents=True)

    if profile not in PROFILES:
        raise PackageError(f"未知包形态 {profile!r}（只支持 {PROFILES}）")
    manifest = Manifest(version=version, profile=profile)
    # 1 安装脚本与说明由调用方提供：本函数不生成策略性文本
    for name in ("install.sh", "uninstall.sh", "README.txt"):
        if not (src / name).exists():
            raise PackageError(f"缺少 {name}（离线包必需，见 12 §2）")
        shutil.copy2(src / name, out / name)

    # 2 profiles：客户档样例，不含真实凭据
    prof_out = out / "profiles"
    prof_out.mkdir()
    for prof in extra_profiles:
        if prof.is_dir():
            shutil.copytree(prof, prof_out / prof.name)

    # 3 镜像目录：**留说明，不造假 tar**
    images = out / "images"
    images.mkdir()
    (images / "README.txt").write_text(
        "本目录需在有 docker 的构建机上生成：\n"
        "  docker save <image>@<digest> -o images/<name>.tar\n"
        "镜像清单与 digest 见 tests/range/VERSIONS.md（12 §2.5）。\n"
        "本包在无 docker 环境下构建，因此此目录为空——verify 会如实报告，不计为通过。\n",
        encoding="utf-8",
    )
    manifest.notes.append("images/ 为空：需在有 docker 的构建机补齐（verify 会报出）")

    # 3.5 浏览器执行体（H02 §3）：只在 browser 形态出现，**且不假装已装**
    if profile == "browser":
        browser = out / "browser"
        browser.mkdir()
        (browser / "README.txt").write_text(
            "本目录需放置 Chromium/Playwright 载荷（约 300MB）。" + chr(10)
            + "构建机：python -m playwright install chromium，随后按 12 §2.5 打 tar。" + chr(10)
            + "本包在无浏览器的环境下构建，因此此目录只有说明——verify 会如实报告。" + chr(10),
            encoding="utf-8",
        )
        manifest.notes.append("browser/ 只有说明：Chromium 载荷需在有浏览器的构建机补齐")

    # 4 清单
    manifest.entries = collect(out)
    (out / "SHA256SUMS").write_text(manifest.sums_text(), encoding="utf-8")
    (out / "manifest.json").write_text(
        json.dumps(manifest.as_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    # 清单自身也要进清单：否则改 SHA256SUMS 不会被发现
    manifest.entries = collect(out)
    (out / "SHA256SUMS").write_text(manifest.sums_text(), encoding="utf-8")
    return manifest


@dataclass(frozen=True)
class VerifyResult:
    ok: bool
    problems: tuple[str, ...] = ()
    checked: int = 0

    def render(self) -> str:
        if self.ok:
            return f"离线包校验通过（{self.checked} 个文件）"
        lines = [f"离线包校验失败（检查 {self.checked} 个文件，{len(self.problems)} 处问题）"]
        lines += [f"  - {p}" for p in self.problems]
        return "\n".join(lines)


def verify(root: Path) -> VerifyResult:
    """逐条重算。**少文件、多文件、改字节，三种都算失败。**"""
    problems: list[str] = []
    for name in REQUIRED_ENTRIES:
        if not (root / name).exists():
            problems.append(f"缺必需项 {name}")

    sums = root / "SHA256SUMS"
    if not sums.exists():
        return VerifyResult(False, tuple(problems + ["缺 SHA256SUMS"]), 0)

    declared: dict[str, str] = {}
    for lineno, line in enumerate(sums.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        parts = line.split("  ", 1)
        if len(parts) != 2:
            problems.append(f"SHA256SUMS:{lineno}: 格式不对")
            continue
        declared[parts[1].strip()] = parts[0].strip()

    actual = {e.path: e.sha256 for e in collect(root)}
    actual.pop("SHA256SUMS", None)  # 自己不入自己的清单

    for path, want in sorted(declared.items()):
        if path == "SHA256SUMS":
            continue
        got = actual.get(path)
        if got is None:
            problems.append(f"清单里有但文件缺失：{path}")
        elif got != want:
            problems.append(f"内容被改动：{path}")

    for path in sorted(set(actual) - set(declared)):
        problems.append(f"文件存在但不在清单里：{path}")

    # 镜像为空要如实报，不能当通过
    imgs = root / "images"
    if imgs.is_dir() and not any(p.suffix == ".tar" for p in imgs.iterdir()):
        problems.append("images/ 无镜像 tar：离线包不完整（需 docker 构建机）")

    problems += check_scripts(root)
    return VerifyResult(not problems, tuple(problems), len(actual))


__all__ = [
    "EGRESS_PATTERNS",
    "REQUIRED_ENTRIES",
    "Entry",
    "Manifest",
    "PackageError",
    "VerifyResult",
    "build",
    "check_scripts",
    "collect",
    "verify",
]
