"""B176：parity 里几条"永远绿"的探针，改成真跑。

判据（同一个问法：**这条检查有没有可能失败？**）：

* V4 覆盖：`from veripen_cli.cmd_coverage import main` → 只要 import 成功就绿；
* E5 并行：只看 `parallel.py` 在不在文件系统里 —— 连"能不能跑"都没验；
* P1 平台：`from veripen_core.workflow import __name__` → 同上。

三条现在都真跑：算矩阵、跑三个任务比指纹、加载 DAG 并检查依赖未满足集。
这一组用例钉住"能红"的那一面——探针在有问题的输入上必须返回 RED。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for extra in (ROOT, ROOT / "tests"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

GREEN = "green"
RED = "red"


def test_v4_matrix_is_really_computed() -> None:
    from veripen_cli.cmd_parity import _coverage

    status, detail = _coverage()
    assert status == GREEN, detail
    assert "类有用例" in detail and "条" in detail, detail


def test_v4_goes_red_on_an_empty_case_library(monkeypatch) -> None:  # noqa: ANN001
    import veripen.bench.core as bench
    import veripen_cli.cmd_parity as parity

    monkeypatch.setattr(bench, "load_cases", lambda *_a, **_k: [])
    status, detail = parity._coverage()
    assert status == RED and "为空" in detail


def test_e5_parallel_probe_compares_fingerprints() -> None:
    from veripen_cli.cmd_parity import _parallel

    status, detail = _parallel()
    assert status == GREEN, detail
    assert "同指纹" in detail


def test_e5_goes_red_when_concurrency_changes_the_result(monkeypatch) -> None:  # noqa: ANN001
    import veripen_core.parallel as parallel
    import veripen_cli.cmd_parity as parity

    calls = {"n": 0}

    def fake_merge(results):  # noqa: ANN001, ANN202
        calls["n"] += 1
        return "parallel" if calls["n"] == 1 else "serial"

    monkeypatch.setattr(parallel, "merge_sha", fake_merge)
    status, detail = parity._parallel()
    assert status == RED and "不一致" in detail


def test_p1_control_plane_runs_the_dag() -> None:
    from veripen_cli.cmd_parity import _control_plane

    status, detail = _control_plane()
    assert status == GREEN, detail
    assert "阶段" in detail and "缺" in detail


def test_p1_goes_red_on_an_empty_workflow(monkeypatch) -> None:  # noqa: ANN001
    import veripen_cli.cmd_parity as parity
    import veripen_core.workflow as workflow

    monkeypatch.setattr(workflow, "load", lambda _p: workflow.Workflow(version=1))
    status, detail = parity._control_plane()
    assert status == RED and "为空" in detail


# ── B177：P3 压缩 / G3 断言表，也都改成"能红"的探针 ─────────────
def test_p3_compression_probe_reports_real_numbers() -> None:
    from veripen_cli.cmd_parity import _compression

    status, detail = _compression()
    assert status == GREEN, detail
    assert "token" in detail and "省" in detail, detail


def test_p3_goes_red_when_compression_inflates(monkeypatch) -> None:  # noqa: ANN001
    import veripen_core.compression as compression
    import veripen_cli.cmd_parity as parity

    class _Out:
        tokens_before = 10
        tokens_after = 99
        reduction = 0.0
        mode = "summarized"

    monkeypatch.setattr(compression, "compress", lambda **_kw: _Out())
    status, detail = parity._compression()
    assert status == RED and "放大" in detail


def test_g3_assertion_table_rows_are_counted() -> None:
    from veripen_cli.cmd_parity import _assertions

    status, detail = _assertions()
    assert status == GREEN, detail
    assert "A1–A20" in detail


def test_g3_goes_red_on_a_truncated_table(monkeypatch, tmp_path) -> None:  # noqa: ANN001
    import veripen_cli.cmd_parity as parity

    plan = tmp_path / "plan-veripen"
    plan.mkdir()
    (plan / "16-完整范围与交付地图.md").write_text(
        "| A1 | x |\n| A2 | y |\n", encoding="utf-8"
    )
    monkeypatch.setattr(parity, "ROOT", tmp_path)
    status, detail = parity._assertions()
    assert status == RED and "缺" in detail and "A3" in detail
