"""oracle 契约（oracle 侧协议）+ 公共基类。

与 core 的 ``Oracle`` Protocol 同形；本模块把它变成"能继承的骨架"，
并补 07 §0.2.1 里 oracle 额外要实现的 ``baseline_params`` / ``baseline_ok``。

**不用 dataclass**：``meta`` 必须是类属性（子类用类级声明覆盖），
dataclass 字段会把它变成实例默认值，导致 ``selector_tags`` 在运行时为空。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar

from pydantic import BaseModel

from hos_ares_core.models import ImpactClass, Judgement, Verdict

from .harness import Pair, Transform, Variant


@dataclass(frozen=True)
class OracleMeta:
    id: str
    version: str
    cwe: str
    impact: ImpactClass
    severity_rule: str
    selector_tags: tuple[str, ...]
    requires: tuple[str, ...] = ()
    #: 有这些标签时优先取用（例：IDOR 偏好 object_read 里参数为整型的那条）
    prefer_tags: tuple[str, ...] = ()


class EmptyParams(BaseModel):
    """无参数 oracle 的空 schema（pydantic 的 BaseModel 不能直接实例化）。"""

    model_config = {"extra": "forbid"}


class BaseOracle:
    """子类只声明 ``meta`` / ``params_model``，只实现 4 个方法。"""

    #: 身份（类级声明，实例不可变）
    meta: ClassVar[OracleMeta] = OracleMeta("", "", "", ImpactClass.READ_ONLY, "", ())
    #: params 模型（None = 空模型）
    params_model: ClassVar[type[BaseModel] | None] = None
    #: 基线是否用注入的真实 token（JWT 类为 True；越权类走账号符号，不设此位）
    baseline_uses_token: ClassVar[bool] = False
    #: 基线**允许**是 4xx（B387）。授权类面的"正常状态"本来就是 401/403——
    #: 不带凭据去请求，被拒才是正确行为。以前 `three_way_holds` 把 4xx 基线一律判成
    #: 不成立，于是 mTLS / BOLA 这类判定永远出不了 VERIFIED。
    #: 默认 False：不改动其余 oracle 的既有语义，要用必须显式声明。
    baseline_accepts_denied: ClassVar[bool] = False
    #: 这条判定**就是在观测失败**（证书/TLS 拒绝）：所有请求都带 error 是**正常现象**，
    #: 不能因此被记成"执行失败"（B393 实测：证书有缺陷的三类靶场全被记成 error）。
    observes_failures: ClassVar[bool] = False

    def burst_sample_count(self) -> int:
        """>0 表示这条判定要走**并发突发**（`judge_burst`），而不是三条件对照。

        这类判据（限流）没有"对照组"可言：它的证据是"并发 N 次全都成功"。
        以前 `sample_burst`/`judge_burst` 只有单测在调，产品路径上永远落
        `use_judge_burst`（B404：语料里那 2 个 FN 就是它）。
        """
        return 0

    def __init__(self, params: BaseModel | None = None, **values: Any) -> None:
        model = self.params_model
        if params is not None:
            self.params = params
        elif model is not None:
            self.params = model(**values)
        else:
            self.params = EmptyParams()

    # ── 协议属性 ────────────────────────────────────────────────
    @property
    def id(self) -> str:
        return self.meta.id

    @property
    def version(self) -> str:
        return self.meta.version

    @property
    def cwe(self) -> str:
        return self.meta.cwe

    @property
    def impact(self) -> ImpactClass:
        return self.meta.impact

    @property
    def requires(self) -> list[str]:
        return list(self.meta.requires)

    @property
    def selector_tags(self) -> list[str]:
        return list(self.meta.selector_tags)

    @property
    def params_schema(self) -> type[BaseModel]:
        return type(self.params)

    def applicable(self, ctx: Any) -> bool:  # noqa: ARG002 - 细判在 plan 里做
        return bool(self.selector_tags)

    # ── 子类必须实现 ────────────────────────────────────────────
    def baseline_params(self, binding: Any) -> dict[str, str]:
        raise NotImplementedError

    def baseline_ok(self, trace: Any) -> bool:
        """基线这一侧算不算成立（第一道门）。

        声明了 `baseline_accepts_denied` 的授权面（B388）：401/403 也算成立——
        不带凭据去请求，被拒才是正确行为。**5xx 永远不算**（那说明基线没有意义）。
        以前这个判断在 `baseline_ok` 与 `three_way_holds` 里各写了一遍，
        改一处另一处还是拒，成了"永远差一步"的根源。
        """
        if trace.is_normal():
            return True
        if not self.baseline_accepts_denied:
            return False
        status = int(getattr(trace, "status", 0) or 0)
        return 400 <= status < 500 and not getattr(trace, "error", None)

    def plan(self, binding: Any) -> list[Variant]:
        raise NotImplementedError

    def judge(self, pairs: list[Pair]) -> Judgement:
        raise NotImplementedError

    # ── 结论构造（oracle 只能给自己敢担保的档位）────────────────
    @staticmethod
    def refuted(reason: str, assertion: dict[str, Any] | None = None) -> Judgement:
        return Judgement(Verdict.REFUTED, reason, assertion or {}, 0.0, "contract")

    @staticmethod
    def verified(reason: str, assertion: dict[str, Any], confidence: float) -> Judgement:
        return Judgement(Verdict.VERIFIED, reason, assertion, confidence, "contract")

    @staticmethod
    def inconclusive(reason: str, assertion: dict[str, Any] | None = None) -> Judgement:
        return Judgement(Verdict.INCONCLUSIVE, reason, assertion or {}, 0.0, "contract")


def transform(kind: str, arg: str = "", target: str = "param_value", note: str = "") -> Transform:
    return Transform(kind=kind, target=target, arg=arg, note=note)


def variant(
    name: str,
    role: str,
    *,
    expect: str,
    tf: Transform | None = None,
    headers: dict[str, str] | None = None,
    account: str = "",
    binding_tag: str = "",
    extra_params: dict[str, str] | None = None,
    method: str = "",
) -> Variant:
    return Variant(
        name=name,
        role=role,
        transform=tf,
        expect=expect,
        headers=headers or {},
        account=account,
        binding_tag=binding_tag,
        extra_params=extra_params or {},
        method=method,
    )
