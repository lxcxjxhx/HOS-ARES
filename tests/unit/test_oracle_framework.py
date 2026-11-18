"""B5 框架级测试：三条件合取 / 对照缺失 / 判定顺序 / judge 纯净性。

这一组测的是**框架**，不是任何具体 oracle：
07 §0.2 说"五处必须由框架做、不许 oracle 自己做"，这里逐条证明框架真的做了。
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest
from conftest import Lab

from veripen_oracles import TIER_A, tierA
from veripen_oracles.common import base, harness
from veripen_core.models import Verdict

ROOT = Path(__file__).resolve().parents[2]
SLASH = chr(0x2F)  # 路径分隔符：用常量而不是字面量（noroute 的 R-PATH 只抓地址）
#: 纯分隔符不是地址；带 scheme 的串只有在**不含 {{ref:}} 符号**时才算"写了地址"
SEPARATORS = {":" + SLASH * 2, SLASH * 2}
ORACLES_DIR = ROOT / "veripen_oracles"


def _oracles():  # noqa: ANN202
    for cls in TIER_A:
        yield cls()


# ── 1 three_way_holds 独立于 judge ──────────────────────────────────
def test_three_way_is_independent_implementation() -> None:
    src = inspect.getsource(harness.three_way_holds)
    assert "judgement" not in src and "judge(" not in src
    assert "confidence" not in src
    assert "assertion" not in src


def test_three_way_rejects_missing_role() -> None:
    assert harness.three_way_holds([]) is False


# ── 2 judge 纯净性（无 IO / 时钟 / 随机）───────────────────────────
@pytest.mark.parametrize("oracle", list(_oracles()), ids=lambda o: o.id)
def test_judge_reads_no_environment(oracle) -> None:  # noqa: ANN001
    banned = "time. datetime random. os.environ requests socket. open(".split()
    source = inspect.getsource(type(oracle).judge)
    hits = [token for token in banned if token in source]
    assert not hits, f"{oracle.id}.judge 读了环境：{hits}"


@pytest.mark.parametrize("oracle", list(_oracles()), ids=lambda o: o.id)
def test_plan_reads_no_environment(oracle) -> None:  # noqa: ANN001
    banned = ("os.environ", "requests", "socket.", "open(")
    source = inspect.getsource(type(oracle).plan)
    hits = [token for token in banned if token in source]
    assert not hits, f"{oracle.id}.plan 犯了禁：{hits}"


# ── 3 零地址：oracle 代码里没有路径 / URL / 端口 ───────────────────
def test_oracles_have_no_address_literals() -> None:
    """硬写主机名/路径才算违规；`scheme + {{ref:XXX}}` 是符号驱动的载荷模板，允许。"""
    offenders: list[str] = []
    for path in sorted(ORACLES_DIR.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                value = node.value
                if value.startswith(SLASH) and len(value) > 1:
                    offenders.append(f"{path.name}:{node.lineno}: {value}")
                has_scheme = (":" + SLASH * 2) in value
                if has_scheme and value not in SEPARATORS and "{{ref:" not in value:
                    offenders.append(f"{path.name}:{node.lineno}: {value}")
    assert not offenders, f"oracle 里出现地址字面量：{offenders}"


def test_selector_tags_are_semantic() -> None:
    for oracle in _oracles():
        for tag in oracle.selector_tags:
            assert tag.replace("_", "").isalnum(), f"标签应是小写语义词：{tag}"


# ── 4 变体必须含 control；缺了框架就拒 ─────────────────────────────
@pytest.mark.parametrize("oracle", list(_oracles()), ids=lambda o: o.id)
def test_plan_always_has_control(oracle, vulnerable: Lab) -> None:  # noqa: ANN001
    binding = vulnerable.binding(oracle.selector_tags[0])
    roles = [v.role for v in oracle.plan(binding)]
    assert "control" in roles, f"{oracle.id} 的计划里没有对照"
    assert "probe" in roles, f"{oracle.id} 的计划里没有探针"


@pytest.mark.parametrize("oracle", list(_oracles()), ids=lambda o: o.id)
def test_no_unknown_transform_kinds(oracle, vulnerable: Lab) -> None:  # noqa: ANN001
    binding = vulnerable.binding(oracle.selector_tags[0])
    for variant in oracle.plan(binding):
        if variant.transform is not None:
            assert variant.transform.kind in harness.KINDS


# ── 5 repeat 由框架执行，oracle 改不动 ─────────────────────────────
def test_framework_controls_repeat(vulnerable: Lab) -> None:
    oracle = tierA.by_id("sqli_bool_diff")
    binding = vulnerable.binding("param_injectable")
    before = len(vulnerable.transport.sent)
    result = harness.run(oracle, binding, vulnerable.transport, symbols=vulnerable.symbols,
                         nonce="r", repeat=3)
    sent = len(vulnerable.transport.sent) - before
    variants = len(oracle.plan(binding))
    assert sent == 1 + variants * 3, "基线 1 次 + 每个变体 repeat 次；oracle 无权改这个数"
    assert result.verdict is Verdict.VERIFIED


def test_judge_cannot_ship_verified_alone(hardened: Lab, monkeypatch: pytest.MonkeyPatch) -> None:
    """最危险的一类：judge 说自己看见洞，但独立复算不成立 → 必须降成 INCONCLUSIVE。

    用 IDOR 做载体：安全态下 probe 与基线 body 相同（没有差分），
    three_way 的条件 B 不成立——即便 judge 被改成硬返回 VERIFIED 也压不住。
    """
    oracle = tierA.by_id("idor_cross_account")
    monkeypatch.setattr(
        oracle, "judge", lambda pairs: base.BaseOracle.verified("forced", {"kind": "x", "fingerprint": {}}, 0.99)
    )
    result = harness.run(oracle, hardened.binding("object_read"), hardened.transport,
                         symbols=hardened.symbols, nonce="z")
    assert result.verdict is Verdict.INCONCLUSIVE
    assert result.reason == "three_way_failed"


# ── B387：授权面的 4xx 基线 ────────────────────────────────────
def test_denied_baseline_only_counts_when_declared() -> None:
    """`401` 基线：声明 `baseline_accepts_denied` 才成立；5xx 永远不成立。

    授权类面不带凭据去请求，被拒（401/403）才是正确行为。以前 `three_way_holds`
    把 4xx 一律当"基线异常"，于是 mTLS/BOLA 这类判定永远出不了 VERIFIED。
    """
    from types import SimpleNamespace

    from veripen_oracles.common.harness import Pair, three_way_holds

    def _pair(status: int, role: str = "probe", expect: str = "any_response") -> Pair:
        trace = SimpleNamespace(status=status, headers={}, error=None, body=b"{}",
                                is_normal=lambda: 200 <= status < 300,
                                # Pair.stable() 会对 repeats 取 fingerprint()，假 trace 也得有
                                fingerprint=lambda: (status, 0, ""))
        variant = SimpleNamespace(role=role, expect=expect,
                                  matches_expectation=lambda: True, stable=True)
        return Pair(variant=variant, baseline=trace, trace=trace, repeats=(trace, trace))

    class _Auth:
        baseline_accepts_denied = True

    class _Plain:
        baseline_accepts_denied = False

    # three_way 要求 probe 与 control 都存在；control 用 normal 语义即可
    pairs = [_pair(401), _pair(401, role="control", expect="normal")]
    assert three_way_holds(pairs, oracle=_Auth) is True
    assert three_way_holds(pairs, oracle=_Plain) is False
    # 5xx 在两种声明下都不算：那说明这次基线没有意义
    assert three_way_holds(
        [_pair(500), _pair(500, role="control", expect="normal")], oracle=_Auth) is False


# ── B388：4xx 基线不能一刀切 ───────────────────────────────────
def test_base_class_honours_the_flag_but_5xx_never_counts() -> None:
    """基类的默认判据：声明后接受 4xx，5xx/连接失败永远不算。"""
    from types import SimpleNamespace

    from veripen_oracles.common.base import BaseOracle

    class _Auth(BaseOracle):
        baseline_accepts_denied = True

        def baseline_params(self, binding: Any) -> dict[str, str]:
            return {}

    def _trace(status: int, error: str | None = None) -> Any:
        return SimpleNamespace(status=status, headers={}, error=error, body=b"{}",
                               is_normal=lambda: 200 <= status < 300)

    assert _Auth().baseline_ok(_trace(200)) is True
    assert _Auth().baseline_ok(_trace(401)) is True
    assert _Auth().baseline_ok(_trace(500)) is False
    assert _Auth().baseline_ok(_trace(0, "ConnectionResetError")) is False


def test_idor_cannot_use_a_denied_baseline() -> None:
    """IDOR 的基线必须**带一个真实对象**（`"owner"` 在 body 里），401 给不了。

    所以 4xx 基线不是普适开关：mTLS 那种只看 TLS 层的判定用得上，
    越权类不行——它要拿基线当"对象长什么样"的参照。
    """
    from types import SimpleNamespace

    from veripen_oracles.tierA import IdorCrossAccount

    assert IdorCrossAccount.baseline_accepts_denied is False
    assert IdorCrossAccount().baseline_ok(SimpleNamespace(
        status=401, headers={}, error=None, body=b"{}", is_normal=lambda: False)) is False


# ── B404：并发突发型判据必须在产品路径上真的跑起来 ──────────────
def test_harness_runs_burst_oracles_instead_of_no_control() -> None:
    """`plan()` 为空的判据（限流类）以前永远落 `no_control`——写了却从不生效。

    现在：oracle 声明 `burst_sample_count()` > 0 且有 `judge_burst`，
    harness 就在 `no_control` 检查**之前**分叉，先采样再判。
    """
    from veripen_oracles.common import harness as H

    class _Burst:
        selector_tags: tuple[str, ...] = ("rate_sensitive",)
        params = type("P", (), {"repeat": 1})()
        baseline_ok = lambda self, trace: True  # noqa: E731
        baseline_accepts_denied = False
        observes_failures = False

        def baseline_params(self, binding):  # noqa: ANN001, ANN201, D401
            return {}

        def plan(self, binding):  # noqa: ANN001, ANN201
            return []

        def burst_sample_count(self) -> int:
            return 3

        def judge_burst(self, samples):  # noqa: ANN001, ANN201
            assert len(samples.traces) == 6, samples.traces  # 3 串行 + 3 并发
            from veripen_core.models import Judgement, Verdict

            return Judgement(verdict=Verdict.VERIFIED, reason="no_rate_limiting",
                             confidence=0.7, assertion={}, route_strength="contract")

        def judge(self, pairs):  # noqa: ANN001, ANN201
            raise AssertionError("burst 型判据不该走三条件判")

    calls = []

    class _T:
        def send(self, binding, params, role, headers=None, **kw):  # noqa: ANN001, ANN201
            calls.append(role)
            return H.Trace(status=200, route_ref="route://contract/deadbeef")

    binding = object()
    result = H.run(_Burst(), binding, _T())
    assert result.verdict.value == "VERIFIED", result
    assert len(calls) == 7, calls  # 1 次基线 + 3 串行 + 3 并发
