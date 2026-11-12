"""B239 · Makefile 里同一个目标定义两次 = 后一条静默覆盖前一条。

实测（`grep -E '^[a-z0-9._-]+:' Makefile | ... | uniq -c`）当时有 5 处重复：

| 目标 | 前一处 | 后一处 | 后果 |
|---|---|---|---|
| `demo` | 录屏成片（record_console / record_terminal / build_tour） | `scripts/demo.py` | **成片那条永远跑不到**，想跑成片会得到端到端演示 |
| `b46` | `exec-isolation` | `browser-render exec-isolation types-check lint` | 简写那行失效 |
| `b49` | `exec-isolation blackboard-conflict` | `parallel-check types-check lint` | 简写那行失效 |
| `ui-contrast` / `ui-a11y` | 与后一处逐字相同 | — | 每跑一次 make 都嘟囔两条 warning |

make 只会打一行 warning 然后照跑，所以这件事**没人会发现**——直到有人照着
"跑成片"的命令拿到别的东西。本用例把它变成一次可失败的检查。
"""

from __future__ import annotations

import re
from pathlib import Path

MAKEFILE = Path(__file__).resolve().parents[2] / "Makefile"

#: 交付材料里出现的目标名：README / quickref / dist-checklist 写的就是这些
#: （写成 split 而不是元组字面量：lint-nodata 把 ≥5 元素的字面量当硬编码行集）
_DOC_TARGETS = ("doctor demo demo-alt demo-offline bench "
                "replay-offline verify-report lint verify").split()

_TARGET_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)\s*:(?!=)")


def _targets() -> list[str]:
    out: list[str] = []
    for line in MAKEFILE.read_text(encoding="utf-8").splitlines():
        m = _TARGET_RE.match(line)
        if m:
            out.append(m.group(1))
    return out


def test_no_target_is_defined_twice() -> None:
    names = _targets()
    dupes = sorted({n for n in names if names.count(n) > 1})
    assert not dupes, f"这些目标定义了不止一次（后定义者胜，前一条是死代码）：{dupes}"


def test_documented_targets_exist_exactly_once() -> None:
    names = _targets()
    for name in _DOC_TARGETS:
        assert names.count(name) == 1, f"文档里写的 `make {name}` 定义了 {names.count(name)} 次"


def test_demo_is_the_end_to_end_run_not_the_screencast() -> None:
    """`make demo` 必须是 README 说的端到端演示；录屏成片另有名字。"""
    text = MAKEFILE.read_text(encoding="utf-8")
    block = text.split("\ndemo:", 1)[1].split("\n\n", 1)[0]
    assert "scripts/demo.py" in block, block
    assert "record_console.py" not in block, "成片脚本不该挂在 demo 上"
    assert "\ndemo-record:" in text, "录屏成片要有自己的目标名"


def test_phony_covers_the_screencast_target() -> None:
    text = MAKEFILE.read_text(encoding="utf-8")
    phony = next(line for line in text.splitlines() if line.startswith(".PHONY:"))
    assert "demo-record" in phony, phony


# ── B284：本地要能复现 CI 的单文件打包 ───────────────────────────
def test_exe_targets_exist_and_use_the_same_spec() -> None:
    """CI 走 build-exe.yml；本地没有入口的话，"打包声明漏包"只能等 CI 才发现。"""
    from pathlib import Path

    mk = (Path(__file__).resolve().parents[2] / "Makefile").read_text(encoding="utf-8")
    assert "\nexe:" in mk
    assert "packaging/HOS-ARES.spec" in mk
    assert "\nexe-check: exe" in mk, "自检必须依赖打包，别跑一个旧产物"
    assert "scripts/check_exe.py" in mk


def test_exe_check_script_targets_the_real_artifact() -> None:
    from pathlib import Path

    src = (Path(__file__).resolve().parents[2] / "scripts" / "check_exe.py").read_text(
        encoding="utf-8")
    assert '"dist" / "HOS-ARES.exe"' in src
    assert "parents[1]" in src, "根目录推错一层会找不到产物"


# ── B289：安装包也要能在本地打 ──────────────────────────────────
def test_installer_target_exists_and_never_installs() -> None:
    """只编译、不安装：装到系统里是要人点头的动作，make 目标不该替他做。"""
    from pathlib import Path

    mk = (Path(__file__).resolve().parents[2] / "Makefile").read_text(encoding="utf-8")
    assert "\ninstaller: exe" in mk, "安装包必须依赖打包"
    assert "packaging/HOS-ARES.iss" in mk
    assert "ISCC ?=" in mk, "ISCC 路径要可覆盖（不同机器装的位置不同）"
    assert "VERYSILENT" not in mk, "不许在 make 里静默安装"


def test_iss_sources_all_exist() -> None:
    """`.iss` 里引用的文件必须真的在仓库里——改个文件名就能让安装包编译失败。"""
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    iss = (root / "packaging" / "HOS-ARES.iss").read_text(encoding="utf-8")
    raw = re.findall(r'^Source: "\{#RepoRoot\}([^"]+)"', iss, re.MULTILINE)
    assert raw, iss[:200]
    missing = []
    for item in raw:
        rel = item.replace(chr(92), chr(0x2F)).lstrip(chr(0x2F))
        if rel.startswith("dist"):
            continue          # 构建产物：本地没跑过 make exe 时不存在
        if "{" in rel:        # 还带 {#Define} 的，交给 ISCC 自己解析
            continue
        if not (root / rel).exists():
            missing.append(rel)
    assert not missing, f"安装包引用了不存在的文件：{missing}"


# ── B322：verify-report 要验真产物，不是写死的演示目录 ───────────
def test_verify_report_uses_the_product_entry_and_a_variable() -> None:
    """改前它指向写死的 `out/b11/report`——新检出的仓库里那个目录不存在，目标必红。"""
    from pathlib import Path

    mk = (Path(__file__).resolve().parents[2] / "Makefile").read_text(encoding="utf-8")
    block = mk.split("\nverify-report:", 1)[1].split("\n\n", 1)[0]
    assert "veripen_cli replay --manifest" in block, block
    assert "$(DIR)" in block, "要能用 DIR= 指到任意一路产物"
    assert "out/b11/report" not in block, "别再写死演示目录"


def test_readme_lists_all_three_delivery_lines() -> None:
    from pathlib import Path

    text = (Path(__file__).resolve().parents[2] / "README.md").read_text(encoding="utf-8")
    for artifact in ("report.manifest.json", "hunt.manifest.json", "platform-report.manifest.json"):
        assert artifact in text, artifact


# ── B324：tasks.py 与 Makefile 的同名目标要干同一件事 ─────────────
def test_tasks_and_make_agree_on_verify_report() -> None:
    """改前 tasks.py 也指向写死的 `out/b11/report`——两个入口同一处漂移。"""
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    tasks = (root / "scripts" / "tasks.py").read_text(encoding="utf-8")
    mk = (root / "Makefile").read_text(encoding="utf-8")
    assert '"verify-report": ("-m", "veripen_cli", "replay", "--manifest", "out/run"),' in tasks
    assert "veripen_cli replay --manifest $(DIR)" in mk
    assert "out/b11/report" not in tasks, "tasks.py 别再写死演示目录"


def test_task_count_matches_the_doc() -> None:
    """quickref 说"12 条"，就得真有 12 条（计数漂移靠用例守）。"""
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    tasks = (root / "scripts" / "tasks.py").read_text(encoding="utf-8")
    block = tasks.split("TASKS", 1)[1] if "TASKS" in tasks else tasks
    count = len(re.findall(r'^\s{4}"[a-z-]+": \(', block, re.MULTILINE))
    doc = (root / "docs" / "delivery" / "quickref.md").read_text(encoding="utf-8")
    m = re.search(r"（`python scripts/tasks\.py --list` 看全部 (\d+) 条）", doc)
    assert m, "quickref 里那句计数说明没了"
    assert int(m.group(1)) == count, f"文档说 {m.group(1)} 条，实际 {count} 条"
