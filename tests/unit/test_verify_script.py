"""B200：闸门清单只有一处——`scripts/verify.py`。

判据：`mypy` + 六道 lint + 账本 + 计划结构一直是**谁记得谁跑**。人手跑意味着两件事
同时发生：漏跑，以及"CI 跑的和本地跑的不是同一串命令"。

这组用例钉四件事（都不依赖外部程序）：
1. 清单可列举，且包含每道闸门；
2. 跳过 ≠ 通过：缺条件时退出码是 2，不是 0；
3. 有失败时退出码是 1，且失败项被点名；
4. `--only` 会拒绝不存在的闸门名（不静默跑空）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
for extra in (ROOT, ROOT / "scripts"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))


EXPECTED = {
    "mypy", "lint-noroute", "lint-nodata", "lint-ui",
    "lint-netimports", "lint-noleak", "spec-drift", "ledger", "plan",
    "plan-old-manifest",
}


def test_list_names_every_gate(capsys: pytest.CaptureFixture[str]) -> None:
    import verify  # type: ignore[import-not-found]

    assert verify.main(["--list"]) == 0
    out = capsys.readouterr().out
    for name in EXPECTED:
        assert name in out, f"清单里少了 {name}"


def test_skip_is_not_pass() -> None:
    """缺条件 → 退出码 2（未全绿），不许记 0。"""
    import verify

    called: list[list[str]] = []

    def runner(argv: list[str]) -> tuple[int, str]:
        called.append(list(argv))
        return 0, ""

    original = verify.available
    verify.available = lambda gate: gate.name != "plan"       # 假装没装 node
    try:
        code = verify.main([], runner=runner)
    finally:
        verify.available = original
    assert code == 2, f"有跳过却返回 {code}"
    assert called, "其余闸门应当照跑"


def test_failure_is_one_and_named(capsys: pytest.CaptureFixture[str]) -> None:
    import verify

    def runner(argv: list[str]) -> tuple[int, str]:
        return (1, "boom") if "ledger_check.py" in argv[-1] else (0, "")

    assert verify.main([], runner=runner) == 1
    out = capsys.readouterr().out
    assert "fail · ledger" in out


def test_unknown_gate_name_is_rejected() -> None:
    import pytest

    import verify

    with pytest.raises(SystemExit) as exc:
        verify.main(["--only", "no-such-gate"])
    assert "未知闸门" in str(exc.value)


def test_all_green_returns_zero() -> None:
    import verify

    assert verify.main([], runner=lambda argv: (0, "")) == 0


# ── B205：bench 冒烟在清单里，并说清"子集不是验收数字" ──────────
def test_bench_smoke_is_in_the_list_and_labelled() -> None:
    import verify

    smoke = [g for g in verify.SMOKES if g.name == "bench-smoke"]
    assert smoke, "冒烟闸门没进清单"
    gate = smoke[0]
    assert gate.kind == "smoke", gate.kind
    assert "--limit" in gate.argv, "子集跑必须显式限制条数"
    assert "--rounds" in gate.argv


def test_smoke_gates_run_without_with_tests(capsys: pytest.CaptureFixture[str]) -> None:
    """冒烟属于快闸门：不带 --with-tests 也要跑到。"""
    import verify

    seen: list[str] = []

    def runner(argv: list[str]) -> tuple[int, str]:
        seen.append(argv[-1])
        return 0, ""

    code = verify.main([], runner=runner)
    out = capsys.readouterr().out
    assert code == 0
    assert "bench-smoke" in out, "冒烟闸门没跑"


# ── B208："lint" 只有一处定义 ──────────────────────────────────
def test_readme_lint_count_matches_the_gate_list() -> None:
    """README 写的 lint 条数必须等于清单里真正的 lint 闸门数。"""
    import re as _re

    import verify

    lints = [g.name for g in verify.GATES if g.name.startswith("lint-") or g.name == "spec-drift"]
    root = Path(__file__).resolve().parents[2]
    text = (root / "README.md").read_text(encoding="utf-8")
    m = _re.search(r"mypy \+ (\d+) 道 lint", text)
    assert m, "README 里没有\"mypy + N 道 lint\"的说法"
    assert int(m.group(1)) == len(lints), f"README 写 {m.group(1)} 道，清单里有 {len(lints)} 道：{lints}"


def test_make_lint_defers_to_the_gate_list() -> None:
    """`make lint` 不许自己再抄一份清单。"""
    root = Path(__file__).resolve().parents[2]
    text = (root / "Makefile").read_text(encoding="utf-8")
    block = text.split("\nlint:", 1)
    assert len(block) == 2, "Makefile 里没有 lint 目标"
    body = block[1].split("\n\n", 1)[0]
    assert "scripts/verify.py --only" in body, f"make lint 没引用 verify 清单：{body!r}"


# ── B232：冻结包自己那份 MANIFEST 也得有人跑 ────────────────────
def test_old_plan_manifest_runs_in_its_own_directory() -> None:
    """校验器读的是相对路径 `MANIFEST.md`，必须在旧包（plan-hos-legacy）那层跑。"""
    import verify

    gate = {g.name: g for g in verify.GATES}["plan-old-manifest"]
    assert gate.needs == "node"
    assert gate.cwd == "plan-hos-legacy", gate.cwd


def test_old_plan_manifest_matches_its_content() -> None:
    """B232 的实物：`ae04456` 改了 `06-证据与replay规格.md` 却没重生成 MANIFEST，

    那张 54 文件的哈希表过期了也没人知道——因为它不在闸门清单里。
    这条断言真跑一次校验器（反例注入实测：往任一规格文件末尾加一个换行即红）。
    """
    import pytest

    import verify

    gate = {g.name: g for g in verify.GATES}["plan-old-manifest"]
    if not verify.available(gate):
        pytest.skip("缺 node")
    status, note = verify.run_gate(gate)
    assert status == verify.PASS, note


# ── B367：日志里要看得见"这是哪个解释器" ────────────────────────
def test_interpreter_note_flags_a_mismatched_virtualenv(tmp_path: Path) -> None:
    """本地有两个环境时，"一堆红"经常只是这个 python 缺依赖。

    实测（本轮）：用全局 python 跑集成 → 6 红、16 skip；换回仓库自带的 `.venv`
    → 全绿。日志里没有解释器，第一反应只会是"改了代码导致回归"。
    """
    import verify

    (tmp_path / ".venv").mkdir()
    notes = verify.interpreter_notes(str(tmp_path / "python.exe"), tmp_path)
    assert notes[0].startswith("python: "), notes
    assert any("仓库自带" in n for n in notes), notes

    inside = tmp_path / ".venv" / "Scripts" / "python.exe"
    notes = verify.interpreter_notes(str(inside), tmp_path)
    assert not any("仓库自带" in n for n in notes), "用对了就别再提示"


def test_gate_children_are_unbuffered() -> None:
    """B425：闸门跑的是 pytest，输出进管道时默认块缓冲——长跑时日志几十分钟不变，
    看的人会当"挂死"（B424 误判两轮）。用子进程**自报**环境变量来钉，不依赖本机行为。
    """
    import os
    import subprocess
    import sys

    from scripts.verify import _default_runner

    code, out = _default_runner(
        [sys.executable, "-c",
         "import os;print(os.environ.get('PYTHONUNBUFFERED',''))"],
        name="unbuffered-probe",
    )
    assert code == 0, out
    assert "1" in out, f"子进程没被解除缓冲：{out!r}（os.environ={os.environ.get('PYTHONUNBUFFERED')!r}）"


def test_build_scripts_have_no_old_package_names() -> None:
    """B430：`.spec` / `.iss` / Makefile / Dockerfile 不在 lint 与用例的扫描范围里。

    八轮改名走过 495 个文件、5553 处，`packaging/HOS-ARES.spec` 与 `Dockerfile` 一次没被碰到——
    而它们恰恰是「产品怎么装、怎么构建」的入口。这条把「构建脚本也要跟改名」钉住。
    """
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    candidates = [
        *root.glob("packaging/*.spec"),
        *root.glob("packaging/*.iss"),
        *root.glob("Dockerfile"),
        *root.glob("Makefile"),
        *root.glob("docker-compose.yml"),
    ]
    assert candidates, "没找到任何构建脚本，扫描表可能失效"
    bad = [p.name for p in candidates
           if "veripen" in p.read_text(encoding="utf-8", errors="replace").lower()]
    assert not bad, f"构建脚本里还有旧包名：{bad}"


# ── B448：`--quick-tests` 只加单测段，`--with-tests` 才加全部三段 ──────
def test_quick_tests_adds_only_the_unit_chunk() -> None:
    """B445 加了 `--quick-tests`：12 项闸门 + 单测（约 2 分钟）。

    钉住两件事：①它确实只加一段；②加的那一段是 `tests-unit` 而不是别的。
    若哪天 `TEST_CHUNKS` 重排，这条会红——那时正该有人看一眼顺序。
    """
    from scripts.verify import TEST_CHUNKS, select

    plain = select("", with_tests=False)
    quick = select("", with_tests=False, quick_tests=True)
    full = select("", with_tests=True)

    assert len(quick) == len(plain) + 1, "quick 应只多一段"
    assert len(full) == len(plain) + len(TEST_CHUNKS), "with-tests 应多出全部测试段"
    assert [g.name for g in quick][-1] == "tests-unit", "quick 加的那段不是单测"
