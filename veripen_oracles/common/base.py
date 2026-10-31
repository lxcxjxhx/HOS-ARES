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

from veripen_core.models import ImpactClass, Judgement, Verdict

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
        return bool(trace.is_normal())

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
    )
