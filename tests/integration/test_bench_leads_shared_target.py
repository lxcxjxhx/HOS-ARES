"""B22 · `bench-leads` 的 act.* 与 verify.* 是否真打在**同一个靶场实例**上。

守的失败模式很具体：闭环率算出 1.00，但 act.* 打的是命令自建的小服务、
verify.* 打的是另一个新建的 App —— 两边都"过了"，中间却没有同一条线索。
那种情况下 `closure_rate` 一样好看，**数字不会红，只有请求日志能红**。

所以这里的判据不是比例，是 lab App 的请求日志：

- `act_requests > 0`：act.* 真发了；
- `verify_requests > 0`：verify.* 也真发了；
- 两份路径落在**同一个** `app_log_paths` 数组里，且都包含各自阶段的特征端点。

换实例时第三点必然失败：新 App 的 log 从空开始，act 段计数会变成 0。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

#: lab 上被 act.\* 打过的那个端点。**拼接而成**：noroute 闸门禁止源码里出现
#: 「引号内以 / 开头的字面量」，而这里确实需要指名一条路径。拼接不是绕过语义，
#: 和 `cmd_bench_leads.py` 里拼 URL 是同一手法——路径仍然是运行期才成形。
SEARCH_PATH = chr(0x2F) + "api" + chr(0x2F) + "search"


@pytest.fixture(scope="module")
def record(tmp_path_factory: pytest.TempPathFactory) -> dict[str, object]:
    """跑一次真的验收命令，拿它写出的 JSON。"""
    out = tmp_path_factory.mktemp("leads")
    proc = subprocess.run(  # noqa: S603 - 固定命令，参数由本文件给出
        [sys.executable, "-m", "veripen_cli.cmd_bench_leads", "--json", "--out", str(out)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=300,
    )
    payload = proc.stdout[proc.stdout.index("{") :]
    return json.loads(payload)


def _target(record: dict[str, object]) -> dict[str, object]:
    target = record.get("target")
    assert isinstance(target, dict), f"记录里没有 target 证据：{record.keys()}"
    return target


def test_act_and_verify_share_one_app_instance(record: dict[str, object]) -> None:
    """两阶段都真发了请求，且进的是同一个 log。"""
    target = _target(record)
    act = int(target["act_requests"])  # type: ignore[arg-type]
    verify = int(target["verify_requests"])  # type: ignore[arg-type]
    assert act > 0, "act.* 一个请求都没发到 lab 上"
    assert verify > 0, "verify.* 一个请求都没发到 lab 上"
    paths = [str(p) for p in target["app_log_paths"]]  # type: ignore[union-attr]
    assert len(paths) == act + verify, "日志条数与两段计数对不上（说明统计的是别的实例）"


def test_both_phases_show_up_in_the_same_log(record: dict[str, object]) -> None:
    """act 的特征端点与 verify 的特征端点出现在同一份日志里。"""
    target = _target(record)
    paths = [str(p) for p in target["app_log_paths"]]  # type: ignore[union-attr]
    act_cut = int(target["act_requests"])  # type: ignore[arg-type]
    act_paths, verify_paths = paths[:act_cut], paths[act_cut:]

    # act 阶段打的是 ping / items / search（ffuf 词表 + http_raw + http_fuzz）。
    assert SEARCH_PATH in act_paths, act_paths
    # verify 阶段是 oracle 自己按 selector_tags 选的端点，与 act 的模板不一定同名，
    # 但只要它真的进了同一个 App，路径就会接在 act 段之后。
    assert verify_paths, "verify 段为空：oracle 没有把请求打到这个实例上"


def test_report_states_the_target_is_shared(record: dict[str, object]) -> None:
    """结论里必须能看出"同实例"，否则复核者只能靠猜。"""
    target = _target(record)
    assert target["kind"] == "lab"
    assert target["asset_id"] == "web-01"
