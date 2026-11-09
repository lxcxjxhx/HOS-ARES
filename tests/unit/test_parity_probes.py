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
