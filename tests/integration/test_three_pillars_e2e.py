"""三支柱端到端：真网络 + 真命令，不只是"命令能 import"。

`parity` 的 G1/G2/G3 验的是"控制台可读 / 包能组 / 断言表齐"。这三条都绕开了
**产品本身能不能在一轮真请求里跑完**。这一批把三支柱各跑一遍，起真 socket：

| 支柱 | 命令 | 这一步要证明什么 |
|---|---|---|
| 渗透 | `run` | 契约 → oracle → capsule → 报告，四段都在盘上 |
| 平台 | `platform` | 多资产 + 审批阻断 + 汇总报告 |
| 对话 | `chat` | 一条指令 → 工具调用 → 文件真被改了 |

靶场用 `tests/lab/net_server.py` 起在本机回环的随机端口——真握手、真响应，
不是 WSGI 进程内调用。地址在运行期注入（`ASSET_A1` 这类符号），不落盘。
"""

from __future__ import annotations

import contextlib
import json
import sys
from pathlib import Path
from typing import Iterator

import pytest

ROOT = Path(__file__).resolve().parents[2]
for extra in (ROOT, ROOT / "tests"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from lab.app import App  # noqa: E402
from lab.net_server import serve  # noqa: E402

CONTRACT = ROOT / "tests" / "fixtures" / "contract" / "sample.json"


def _contract_for(base: str, tmp: Path) -> Path:
    """把契约里的 `servers[].url` 换成靶场地址。

    契约校验是 fail-closed 的：`servers` 里的 host 必须是本次 assets 之一
    （"契约 host 不在 assets 内"直接退出码 2）。所以跑真 socket 时必须换掉它，
    否则验的是"契约与目标不匹配能不能拒"，不是"三支柱能不能跑完"。
    """
    spec = json.loads(CONTRACT.read_text(encoding="utf-8"))
    spec["servers"] = [{"url": base}]
    path = tmp / "openapi.json"
    path.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
    return path


def test_contract_path_runs_end_to_end_on_a_matching_spec(
    lab_base: str, tmp_path: Path
) -> None:
    """**契约路径真的能跑通**（B544）——这是 B540 留下的那个洞。

    B540 只钉住了「契约与目标不匹配时体面收场」。这一条补另一半：
    用靶场自己声明的面清单转出配套 OpenAPI（`tests/lab/contract.py`），
    契约 → oracle → capsule → 报告整条链路在真 socket 上跑一遍。

    与 `--discover` 那条的区别：这条**走契约**，所以标签来自 `x-hos-ares-tags`
    （置信 0.95），而不是从观测形状推的（0.80）。两条路径都得能跑，才算三支柱真的能用。
    """
    from lab.app import assets_json
    from lab.contract import to_openapi

    spec = tmp_path / "lab-openapi.json"
    spec.write_text(
        json.dumps(to_openapi(assets_json(), base_url=lab_base), ensure_ascii=False),
        encoding="utf-8",
    )
    out = tmp_path / "run"
    code, text = _run_cli([
        "run", "--target", lab_base, "--openapi", str(spec), "--out", str(out),
    ])
    assert code == 0, f"run 退出码 {code}\n{text[-1500:]}"
    findings = json.loads((out / "findings.json").read_text(encoding="utf-8"))
    assert findings["counts"]["by_outcome"].get("reported", 0) > 0, (
        f"配套契约下仍然一条都没报：{findings['counts']}\n{text[-900:]}"
    )
    caps = list((out / "capsules").glob("*.json"))
    assert caps, "有 reported 却没有 capsule：证据没落盘"
    # 契约来源必须如实标出来，不能与观测推断混为一谈
    strengths = {f["route"]["strength"] for f in findings["findings"]}
    assert "contract" in strengths, strengths


def test_contract_path_degrades_honestly_when_paths_do_not_match(
    lab_base: str, tmp_path: Path
) -> None:
    """契约与目标**路径对不上**时：退出码 0、四份交付物都在、**一条证据都没有**。

    这不是端到端成功的证明，恰恰相反——它钉住的是"体面地承认没测出来"：
    `sample.json` 的路径（/api/items 等）与靶场的响应形状对不上，基线全 abnormal，
    八条 oracle 全是 `undetermined`。此时：

    - 不许把 `undetermined` 写成 VERIFIED（那是 H1 红线）；
    - 也不许因为"没测出东西"就崩（那会让调用方分不清"跑挂了"与"没面可测"）；
    - `findings.json` 的 `counts` 必须如实反映 `reported == 0`。

    这条用例的存在本身也是记录：**为什么三支柱的端到端走 `--discover` 而不是 `--openapi`**。
    """
    out = tmp_path / "run"
    code, text = _run_cli([
        "run", "--target", lab_base, "--openapi", str(_contract_for(lab_base, tmp_path)),
        "--out", str(out),
    ])
    assert code == 0, f"契约路径不对不该崩：{code}\n{text[-800:]}"
    findings = json.loads((out / "findings.json").read_text(encoding="utf-8"))
    by_outcome = findings["counts"]["by_outcome"]
    assert by_outcome.get("reported", 0) == 0, by_outcome
    assert all(f["outcome"] != "reported" for f in findings["findings"]), "没测出来却报了结论"
    caps = list((out / "capsules").glob("*.json")) if (out / "capsules").exists() else []
    assert not caps, f"没有 reported 却有 {len(caps)} 份 capsule"


@pytest.fixture()
def lab_base() -> Iterator[str]:
    """真 socket 上的脆弱靶场；用完就停。

    `serve()` 是**生成器**（返回 base URL 再让出），不是 contextmanager 装饰过的——
    直接 `with serve(app)` 会报 `generator does not support the context manager protocol`。
    """
    if not CONTRACT.exists():
        pytest.skip("缺契约夹具")
    gen = serve(App())
    base = next(gen)
    try:
        yield base
    finally:
        contextlib.suppress(StopIteration)
        with contextlib.suppress(StopIteration):
            next(gen)


def _run_cli(argv: list[str], stdin_text: str = "") -> tuple[int, str]:
    """进程内跑 CLI（不 shell 出去）：返回 (退出码, 合并输出)。

    `chat` 从 stdin 读指令（`input()`），所以喂法是把文本塞进 sys.stdin，
    末尾再补一个 `quit`——否则会一直等下一句。
    """
    import io

    from hos_ares_cli.__main__ import main

    buf = io.StringIO()
    old_stdin = sys.stdin
    if stdin_text:
        sys.stdin = io.StringIO(stdin_text + "\nquit\n")
    try:
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            try:
                code = main(argv)
            except SystemExit as exc:          # 命令用 SystemExit 收尾
                code = int(exc.code or 0)
    finally:
        sys.stdin = old_stdin
    return int(code or 0), buf.getvalue()


# ── 支柱一：渗透（run）────────────────────────────────────────
def test_run_produces_all_four_deliverables(lab_base: str, tmp_path: Path) -> None:
    """契约 → oracle → capsule → 报告，四段都在盘上。

    走 `--discover`（无契约）而不是 `--openapi`：`tests/fixtures/contract/sample.json`
    的路径与靶场对不上，基线直接 abnormal，八条 oracle 全 `undetermined`——
    那验的是"契约不匹配时能不能体面收场"，不是"三支柱能不能跑完"。
    无契约路径由 crawler 从首页攒面，与靶场天生配套（`test_capsule_chain_multi_face`
    同款做法）。
    """
    out = tmp_path / "run"
    code, text = _run_cli([
        "run", "--target", lab_base, "--discover",
        "--discover-rounds", "2", "--discover-pages", "8", "--out", str(out),
    ])
    assert code == 0, f"run 退出码 {code}\n{text[-1500:]}"
    for name in ("report.md", "findings.json", "report.sarif", "report.manifest.json"):
        assert (out / name).exists(), f"缺交付物 {name}；输出：{text[-600:]}"
    caps = list((out / "capsules").glob("*.json"))
    assert caps, f"一条证据都没有（那一轮没跑出判定）\n{text[-900:]}"
    findings = json.loads((out / "findings.json").read_text(encoding="utf-8"))
    verified = [f for f in findings["findings"] if f.get("outcome") == "reported"]
    assert verified, "盘上有 capsule 但 findings 里没有 reported：证据与结论对不上"


def test_run_report_carries_no_address(lab_base: str, tmp_path: Path) -> None:
    """报告里不许出现目标地址：地址只在运行期存在（V3 的商品化体现）。"""
    out = tmp_path / "run"
    _run_cli(["run", "--target", lab_base, "--discover",
              "--discover-rounds", "2", "--discover-pages", "8", "--out", str(out)])
    report = (out / "report.md").read_text(encoding="utf-8")
    host = lab_base.split(":", 2)[1].lstrip(chr(0x2F)) + ":" + lab_base.rsplit(":", 1)[1]
    assert host not in report, "目标主机串进了报告"


# ── 支柱二：平台（platform）──────────────────────────────────
def test_platform_runs_every_asset(lab_base: str, tmp_path: Path) -> None:
    """`--profile` 与 `--targets` 二选一（档里已有 assets）。

    `--targets` 收的是**逗号分隔的目标列表**（纯地址），asset id 由平台自己编号
    （`asset-1`、`asset-2`…）。写成 `id=url` 会被当成"不是合法地址"整批拒掉。
    """
    out = tmp_path / "platform"
    code, text = _run_cli([
        "platform", "--targets", lab_base, "--discover", "--out", str(out),
    ])
    assert code == 0, f"platform 退出码 {code}\n{text[-1500:]}"
    assert (out / "platform-report.md").exists()
    assert (out / "platform-report.manifest.json").exists()


def test_platform_blocks_when_approval_required(lab_base: str, tmp_path: Path) -> None:
    """审批 PENDING 时退出码必须是 3（与"跑挂了"分开，CI 才能不报警）。"""
    out = tmp_path / "platform-approval"
    code, _text = _run_cli([
        "platform", "--targets", lab_base, "--discover", "--out", str(out),
        "--require-approval",
    ])
    assert code == 3, f"要求审批但退出码是 {code}"


# ── 支柱三：对话（chat）──────────────────────────────────────
def test_chat_executes_a_tool_and_touches_the_file(tmp_path: Path) -> None:
    """规则模式（不配模型）下，一句"往 X 写 Y"必须真的把文件写出来。"""
    target = tmp_path / "made.txt"
    code, text = _run_cli(
        ["chat", "--root", str(tmp_path), "--yes"], stdin_text=f"往 {target.name} 写 hello"
    )
    assert code == 0, f"chat 退出码 {code}\n{text[-800:]}"
    assert target.exists(), f"文件没被写出来\n{text[-800:]}"
    assert "hello" in target.read_text(encoding="utf-8")


def test_chat_without_model_says_so(tmp_path: Path) -> None:
    """没配模型就显示 `model: rules`——不假装调了模型。"""
    code, text = _run_cli(["chat", "--root", str(tmp_path)], stdin_text="quit")
    assert code == 0, text[-800:]
    assert "rules" in text, text[:400]
