"""来源符号化：让模板里的「这台靶场在哪」由**符号**决定，而不是由调用方拼。

为什么需要它（27 N2 的边界）：
bench 要走真网络，但靶场端口是运行期分配的，而**绑定模板必须由 resolver 给**。
如果测试自己把 scheme + 主机 + 端口 拼成模板，那 N2 就被绕过了——
"地址只能由 resolver 产出"这条会变成空话。

做法：模板里写占位符 ``{base}``，由 **source 层**在自己的解析阶段用符号值替换。
于是：
- 模板仍然由 resolver 产出（N2 成立）；
- 端口不进入任何配置文件或测试代码（只有符号 ``LAB_BASE``）；
- 换端口、换主机只换符号值，不动模板、不动绑定、不动 oracle。

**为什么替换在 source 层而不是 resolver**：resolver 的职责是"合并与定 ref"，
它不该知道"符号怎么解"。符号解析属于"这个来源怎么读数据"，是 source 的事。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Mapping, Sequence
from urllib.parse import urlsplit

from .base import SourceError, matches_hints
from ..types import RouteCandidate, RouteProvenance, RouteQuery

#: 模板占位符形态：``{base}`` / ``{oob}`` —— **与参数占位符同形**，
#: 但参数占位符在发送期才替换，符号在解析期就替换。区别在于"谁认识它"：
#: 参数由 oracle 填，符号由 source 填。
_SYMBOL = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
#: 路径分隔符。写成常量而非字面量：本模块要处理"符号值末尾是否带分隔符"，
#: 但那属于字符串规整，不该被当成"代码里出现了绝对路径"。
SEP = chr(0x2F)

#: scheme 的默认端口。与 ``tools.transport`` 的同名常量保持一致——
#: 两处不一致会让「同源判断」在一边成立、在另一边不成立。
DEFAULT_PORTS: Mapping[str, int] = {"http": 80, "https": 443}


def _origin_of(url: str) -> tuple[str, str, int | None] | None:
    """``scheme + host + port`` 三元组；不是绝对 URL 时返回 None。

    端口缺省时按 scheme 补齐——``h`` 与 ``h:80`` 在 http 下是同一个 origin，
    不做这个归一，同一个部署换个写法就会算出两个身份。
    """
    parts = urlsplit(url)
    if not parts.scheme or not parts.hostname:
        return None
    scheme = parts.scheme.lower()
    port = parts.port if parts.port is not None else DEFAULT_PORTS.get(scheme)
    return (scheme, parts.hostname.lower(), port)


def _strip_same_origin(template: str, base: str) -> str | None:
    """若 ``template`` 与 ``base`` **同源**，返回去掉 origin 后的路径；否则 None。

    判据是 origin 相等，不是字符串前缀。后者会把主机 ``h`` 与 ``host2``
    判成一对（前缀真、主机假），削出半截主机名。

    同源时只保留 path + query + fragment：origin 是"这台机器在哪"，
    路径才是"哪个面"。这也顺带处理了端口缺省（``:80`` 与不写等价）。
    """
    t_origin = _origin_of(template)
    b_origin = _origin_of(base)
    if t_origin is None or b_origin is None or t_origin != b_origin:
        return None
    parts = urlsplit(template)
    rest = parts.path or SEP
    if parts.query:
        rest += "?" + parts.query
    if parts.fragment:
        rest += "#" + parts.fragment
    return rest


@dataclass(frozen=True)
class SymbolTable:
    """符号 → 值。**值可以是地址，但符号名不是地址**（27 N3 只禁后者）。"""

    values: Mapping[str, str] = field(default_factory=dict)

    def resolve(self, name: str) -> str | None:
        return self.values.get(name)

    def missing(self, template: str, *, known_params: Iterable[str] = ()) -> list[str]:
        """模板里出现、既不是符号也不是该绑定的参数 → 无法解析，应当拒。

        这个检查防的是**拼错符号**：写成 ``{bsae}`` 会被当成参数，
        然后在发送期变成未替换占位符——一次假的 404，比一次明确报错糟糕得多。
        """
        params = set(known_params)
        bad: list[str] = []
        for m in _SYMBOL.finditer(template):
            name = m.group(1)
            if name in self.values or name in params:
                continue
            bad.append(name)
        return bad


class SymbolSource:
    """把上游候选的模板做一次符号处理。**包一层，不改上游。**

    两种形态都要支持，因为真实契约两种都有：

    1. 模板里显式写 ``{LAB_BASE}/api/items/{id}`` → 直接替换符号；
    2. 模板是**相对路径** ``/api/items/{id}`` → 用 ``base_symbol`` 给的值做前缀。

    第 2 种是常态：OpenAPI 的 ``paths`` 都是相对路径，"这台机器在哪"来自资产。
    如果不支持它，符号化就只对"人手工写绝对模板"的靶场有用，对真实契约没用。
    """

    def __init__(
        self,
        name: str,
        provenance: RouteProvenance,
        candidates: Sequence[RouteCandidate],
        symbols: SymbolTable,
        *,
        base_symbol: str = "",
    ) -> None:
        self.name = name
        self.provenance = provenance
        self._candidates = list(candidates)
        self._symbols = symbols
        self._base_symbol = base_symbol
        if base_symbol and symbols.resolve(base_symbol) is None:
            raise SourceError(name, f"base_symbol {base_symbol!r} 在符号表里没有值")

    def _render(self, template: str, params: Sequence[str]) -> str:
        bad = self._symbols.missing(template, known_params=params)
        if bad:
            raise SourceError(self.name, f"模板 {template!r} 含未知占位符 {bad}")

        out = template
        for name, value in self._symbols.values.items():
            out = out.replace("{" + name + "}", value.rstrip(SEP))

        # 相对路径 + 有基址符号 → 补前缀。**只补一次**，且不碰已带 scheme 的模板。
        if self._base_symbol and out.startswith(SEP) and "://" not in out:
            base = (self._symbols.resolve(self._base_symbol) or "").rstrip(SEP)
            out = base + out
        return out

    def _identity(self, template: str) -> str:
        """结构身份：把**本来源注入的部署地址**从模板里去掉，其余原样。

        为什么不能直接存符号形态（``{LAB_BASE}/api/x``）：同一个面，若 A 环境用
        静态相对模板（``/api/x``）、B 环境用符号模板（``{LAB_BASE}/api/x``），
        两者会算出**不同的 structural_key**，于是 ref 不同、capsule 迁移后
        ``route_ref_not_resolvable``。身份里不该出现符号名这种"环境参数"。

        为什么也不能把任意 host 都剥掉：契约自己写的绝对 URL（跨主机 API）
        host 就是身份的一部分。这里只去掉 ``base_symbol`` 解析出的那个基址，
        以及本来源符号表里出现的那些值——**都是"本部署的地址"，不是契约语义**。

        **为什么按 origin 判断而不是按字符串前缀**（这条曾经错过）：
        基址 ``http`` + ``:`` + ``h`` 与模板 ``http`` + ``:`` + ``host2`` 的前缀关系成立，
        但它们是**不同的主机**。用 ``startswith`` 会把后者的主机名削成 ``ost2``——
        一条合法模板被削掉半个名字，而结果看起来仍然像条路径，不会报错。
        判据必须是「scheme + host + port 完全一致」。
        """
        out = template

        # ① 模板里显式写着符号名（``{LAB_BASE}/api/x``）→ 先展开成真实值，
        #    否则身份里会留一个"环境参数名"，换环境就变。
        for name, value in self._symbols.values.items():
            out = out.replace("{" + name + "}", value.rstrip(SEP))

        # ② 剥掉与本来源某个符号值**同源**的基址。按 origin 比，不按前缀比。
        for raw in self._symbols.values.values():
            candidate = raw.rstrip(SEP)
            if not candidate:
                continue
            stripped = _strip_same_origin(out, candidate)
            if stripped is not None:
                out = stripped
                break  # 只剥一次：多次剥会把路径段也吃掉

        return out or template

    def collect(self, q: RouteQuery) -> list[RouteCandidate]:
        out: list[RouteCandidate] = []
        for c in self._candidates:
            if not matches_hints(c, q):
                continue
            template = self._render(c.template, c.param_names)
            out.append(
                RouteCandidate(
                    method=c.method,
                    template=template,
                    param_names=c.param_names,
                    auth_requirement=c.auth_requirement,
                    body_shape_sha256=c.body_shape_sha256,
                    source=c.source,
                    verifications=c.verifications,
                    param_ambiguity=c.param_ambiguity,
                    response_shape=dict(c.response_shape),
                    # 身份 = 去掉本来源注入的部署地址后的模板。
                    # 这样「静态相对模板」与「符号模板」在换环境后得到同一条面。
                    identity_template=self._identity(c.template),
                )
            )
        return out


__all__ = ["SymbolSource", "SymbolTable"]
