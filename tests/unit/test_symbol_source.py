"""来源符号化测试（27 N2 的边界验证）。

这批要证明的命题只有一句：
**端口可以不进配置文件、不进测试代码、不进绑定模板，而绑定仍然由 resolver 产出。**

如果做不到，bench 走真网络就得靠测试自己拼地址——那 N2 就成了空话。
"""

from __future__ import annotations

import pytest

from veripen_core.routes import (
    AssetRef,
    Resolver,
    RouteHints,
    RouteProvenance,
    RouteQuery,
)
from veripen_core.routes.sources.base import StaticSource
from veripen_core.routes.sources.symbols import SymbolSource, SymbolTable
from veripen_core.routes.types import RouteCandidate

SEP = chr(0x2F)
BASE_SYM = "LAB_BASE"


def _cand(template: str, params: tuple[str, ...] = ("id",)) -> RouteCandidate:
    return RouteCandidate(
        method="GET", template=template, param_names=params,
        source=RouteProvenance.CONTRACT, response_shape={"semantic_tags": ["object_read"]},
    )


def _resolve(sources, tags: tuple[str, ...] = ("object_read",)):  # noqa: ANN001
    q = RouteQuery(asset=AssetRef(id="web-01"), hints=RouteHints(), min_confidence=0.0)
    rs = Resolver(sources=sources).resolve(q, now=1000)
    return [b for b in rs.bindings if tags[0] in getattr(b, "semantic_tags", ())]


# ══════════════════════════════════════════════════════════════════
# 1 符号替换发生在 source 层
# ══════════════════════════════════════════════════════════════════
def test_symbol_is_substituted_before_resolver() -> None:
    syms = SymbolTable({BASE_SYM: "http" + ":" + SEP * 2 + "lab.invalid:9000"})
    src = SymbolSource("contract", RouteProvenance.CONTRACT,
                       [_cand("{" + BASE_SYM + "}" + SEP + "api" + SEP + "items" + SEP + "{id}")],
                       syms)
    got = src.collect(RouteQuery(asset=AssetRef(id="a")))
    assert got[0].template == "http" + ":" + SEP * 2 + "lab.invalid:9000" + SEP + "api" + SEP + "items" + SEP + "{id}"
    # 参数占位符必须原样保留——它由 oracle 填，不是符号
    assert "{id}" in got[0].template
    assert BASE_SYM not in got[0].template


def test_unknown_symbol_is_rejected_loudly() -> None:
    """拼错符号会退化成"未替换参数"，然后在发送期变成假 404。这里提前拒。"""
    src = SymbolSource("contract", RouteProvenance.CONTRACT,
                       [_cand("{bsae}" + SEP + "x")], SymbolTable({}))
    with pytest.raises(Exception, match="未知占位符"):
        src.collect(RouteQuery(asset=AssetRef(id="a")))


def test_param_names_are_not_treated_as_symbols() -> None:
    syms = SymbolTable({BASE_SYM: "http" + ":" + SEP * 2 + "h"})
    src = SymbolSource("contract", RouteProvenance.CONTRACT,
                       [_cand("{" + BASE_SYM + "}" + SEP + "{id}", params=("id",))], syms)
    assert src.collect(RouteQuery(asset=AssetRef(id="a")))[0].template.endswith("{id}")


def test_no_symbol_means_candidate_untouched() -> None:
    src = SymbolSource("contract", RouteProvenance.CONTRACT,
                       [_cand(SEP + "api" + SEP + "{id}")], SymbolTable({}))
    assert src.collect(RouteQuery(asset=AssetRef(id="a")))[0].template == SEP + "api" + SEP + "{id}"


# ══════════════════════════════════════════════════════════════════
# 2 绑定仍由 resolver 产出（N2）
# ══════════════════════════════════════════════════════════════════
def test_binding_still_constructed_by_resolver() -> None:
    """符号替换后，绑定依然由 Resolver 造——**调用方拿不到"自己拼的模板"**。"""
    syms = SymbolTable({BASE_SYM: "http" + ":" + SEP * 2 + "lab.invalid"})
    src = SymbolSource("contract", RouteProvenance.CONTRACT,
                       [_cand("{" + BASE_SYM + "}" + SEP + "api" + SEP + "items" + SEP + "{id}")],
                       syms)
    got = _resolve([src])
    assert got, "resolver 没产出绑定"
    b = got[0]
    assert b.ref.startswith("route://")
    assert len(b.template_sha256) == 64
    assert BASE_SYM not in b.template


def test_symbol_values_do_not_change_structural_identity() -> None:
    """换端口 = 换部署地址，但**不换"这是什么面"**。

    这条测试原来断言的是相反的：``template_sha256`` 随端口变、``ref`` 不变。
    那自相矛盾——``ref = opaque16(structural_key)``，而 ``structural_key``
    里含 ``normalize_template(template)``，与 ``template_sha256`` 同源。
    一个变一个不变，只可能是因为结构键从**另一个**模板算的，而当时它并没有。

    规格（27 §3.4）说得很直接：``ref`` 由结构键派生，是为了
    「同一条面在不同来源/不同轮次里拿到同一个 ref；这是 replay 能对上号的根」。
    换一台机器同一条面还是同一条面——所以端口不该进身份。

    现在的分工：``identity_template`` 参与身份（相对模板 / 契约原样），
    ``template`` 只负责"打哪儿"。两者都在绑定上，可分别核对。
    """
    def run(port: int):  # noqa: ANN202
        syms = SymbolTable({BASE_SYM: "http" + ":" + SEP * 2 + "lab.invalid:" + str(port)})
        src = SymbolSource("contract", RouteProvenance.CONTRACT,
                           [_cand("{" + BASE_SYM + "}" + SEP + "api" + SEP + "{id}")], syms)
        return _resolve([src])[0]

    a, b = run(9000), run(9001)
    # 身份三项必须一致（结构键 / 模板哈希 / ref）
    assert a.structural_key == b.structural_key
    assert a.template_sha256 == b.template_sha256
    assert a.ref == b.ref
    # 执行地址必须不同——否则"换机器"这件事没生效
    assert a.template != b.template
    assert "9000" in a.template and "9001" in b.template
    # 身份模板里不含端口：它保留符号形态，等下一台机器再替换
    assert BASE_SYM in a.identity_template or "9000" not in a.identity_template


def test_same_symbol_value_is_reproducible() -> None:
    def run():  # noqa: ANN202
        syms = SymbolTable({BASE_SYM: "http" + ":" + SEP * 2 + "lab.invalid:9000"})
        src = SymbolSource("contract", RouteProvenance.CONTRACT,
                           [_cand("{" + BASE_SYM + "}" + SEP + "api" + SEP + "{id}")], syms)
        return _resolve([src])[0]

    a, b = run(), run()
    assert a.template_sha256 == b.template_sha256
    assert a.ref == b.ref


def test_three_template_forms_share_one_identity() -> None:
    """同一个面用三种模板写法表达时，必须算出**同一个 ref**。

    三种写法都是真实会出现的：
    1. 静态来源的相对模板 ``/api/users/{id}``；
    2. 符号模板 ``{LAB_BASE}/api/users/{id}``（人手工写绝对模板的靶场）；
    3. 相对模板 + ``base_symbol``（OpenAPI 的常态：paths 全是相对路径）。

    若三者身份不同，同一个面在 A 环境录的 capsule 到 B 环境就
    ``route_ref_not_resolvable``——迁移能力直接失效，而且不会有人立刻发现。
    """
    def ref_of(template: str, base: str, base_sym: str = ""):  # noqa: ANN202
        cand = RouteCandidate(
            method="GET", template=template, param_names=("id",),
            source=RouteProvenance.CONTRACT,
            response_shape={"semantic_tags": ["object_read"]},
        )
        src = SymbolSource(
            "contract", RouteProvenance.CONTRACT, [cand],
            SymbolTable({BASE_SYM: base}), base_symbol=base_sym,
        )
        q = RouteQuery(asset=AssetRef(id="web-01"), hints=RouteHints(), min_confidence=0.0)
        return Resolver(sources=[src]).resolve(q, now=1000).bindings[0]

    relative = SEP + "api" + SEP + "users" + SEP + "{id}"
    bases = ["http" + ":" + SEP * 2 + "a.invalid:1111",
             "http" + ":" + SEP * 2 + "b.invalid:2222"]

    refs = {
        ref_of(relative, b, BASE_SYM).ref for b in bases
    } | {
        ref_of("{" + BASE_SYM + "}" + relative, b).ref for b in bases
    }
    assert len(refs) == 1, f"同一面被算成了多条：{refs}"

    # 身份模板必须是**不含部署地址**的相对形态
    got = ref_of("{" + BASE_SYM + "}" + relative, bases[0])
    assert got.identity_template == relative, got.identity_template
    assert bases[0] not in got.identity_template


def test_contract_absolute_url_keeps_its_host_in_identity() -> None:
    """契约自己写的绝对 URL：host 是身份的一部分，**不剥**。

    这与上面的边界必须同时成立，否则"排除部署地址"会滑成"排除所有 host"，
    把虚拟主机合并成一个面——那是制造 FP。
    """
    def ref_of(host: str):  # noqa: ANN202
        cand = RouteCandidate(
            method="GET",
            template="https" + ":" + SEP * 2 + host + SEP + "api" + SEP + "x",
            source=RouteProvenance.CONTRACT,
            response_shape={"semantic_tags": ["api_read"]},
        )
        src = SymbolSource("contract", RouteProvenance.CONTRACT, [cand], SymbolTable({}))
        q = RouteQuery(asset=AssetRef(id="web-01"), hints=RouteHints(), min_confidence=0.0)
        return Resolver(sources=[src]).resolve(q, now=1000).bindings[0]

    a, b = ref_of("api.example.invalid"), ref_of("admin.example.invalid")
    assert a.ref != b.ref, "两个主机的同名路径被合并成一条面了"
    assert "api.example.invalid" in a.identity_template



    """整套 RouteSet 的 ``routeset_sha256`` 不随部署地址变。

    这是 ``ref`` 稳定的**加强版**：``routeset_sha256`` 是内容身份，同时被用作
    缓存键（27 §3.7）与 N6 的三轮一致性锚。若它把 ``template``（含注入的
    host/port）算进去，换一台机器就得到另一个 sha —— 缓存全失效，且
    capsule 的 route 块对不上旧记录。

    这里比较三个完全不同的基址：端口不同、主机不同、scheme 不同。
    三者必须给出同一个 sha，同时**执行地址必须各不相同**（否则说明根本没替换）。
    """
    def whole(base: str):  # noqa: ANN202
        syms = SymbolTable({BASE_SYM: base})
        src = SymbolSource("contract", RouteProvenance.CONTRACT,
                           [_cand("{" + BASE_SYM + "}" + SEP + "api" + SEP + "{id}")], syms)
        q = RouteQuery(asset=AssetRef(id="web-01"), hints=RouteHints(), min_confidence=0.0)
        return Resolver(sources=[src]).resolve(q, now=1000)

    bases = ["http" + ":" + SEP * 2 + "lab.invalid:9000",
             "http" + ":" + SEP * 2 + "lab.invalid:9001",
             "https" + ":" + SEP * 2 + "elsewhere.invalid:443"]
    sets = [whole(b) for b in bases]

    shas = {s.routeset_sha256 for s in sets}
    assert len(shas) == 1, f"换环境换了内容身份：{[s[:12] for s in shas]}"

    templates = {s.bindings[0].template for s in sets}
    assert len(templates) == len(bases), f"执行地址没有被替换：{templates}"


# ══════════════════════════════════════════════════════════════════
# 3 符号表本身
# ══════════════════════════════════════════════════════════════════
def test_prefix_sharing_hosts_are_not_confused() -> None:
    """主机名是另一个主机的**前缀**时，不许当成同一个 origin。

    短主机 ``h`` 与 ``host2`` 的前缀关系成立，但它们是两台不同的机器。
    早先按 ``startswith`` 剥基址，会把 ``host2`` 削成 ``ost2``——
    模板仍像条路径，不报错，只是身份变了。这类错误最麻烦的地方在于
    **它不抛异常**：只会让某条面在换环境后 ref 对不上。

    修法是按 origin 三元组比较。这条测试直接钉住那个反例。
    """
    def ident(host: str, template_host: str) -> str:  # noqa: ANN202
        base = "http" + ":" + SEP * 2 + host + ":8080"
        tpl = "http" + ":" + SEP * 2 + template_host + ":8080" + SEP + "api" + SEP + "x"
        cand = RouteCandidate(
            method="GET", template=tpl, source=RouteProvenance.CONTRACT,
            response_shape={"semantic_tags": ["api_read"]},
        )
        src = SymbolSource(
            "contract", RouteProvenance.CONTRACT, [cand], SymbolTable({BASE_SYM: base}),
        )
        q = RouteQuery(asset=AssetRef(id="web-01"), hints=RouteHints(), min_confidence=0.0)
        return Resolver(sources=[src]).resolve(q, now=1000).bindings[0].identity_template

    # 同源：剥掉 origin，只留路径
    assert ident("lab", "lab") == SEP + "api" + SEP + "x"
    # 前缀关系但不同源：必须**原样保留**，不能被削
    got = ident("h", "host2")
    assert "host2" in got, f"主机名被前缀剥离削掉了：{got}"


def test_default_port_is_normalized_in_origin() -> None:
    """``h`` 与 ``h:80`` 在 http 下是同一个 origin。

    不归一的话，同一个部署换个写法（带不带默认端口）就会算出两个身份，
    而两次解析看起来都"成功"——ref 不同只有迁移时才发现。
    """
    def ident(base: str) -> str:  # noqa: ANN202
        cand = RouteCandidate(
            method="GET", template="http" + ":" + SEP * 2 + "h:80" + SEP + "api" + SEP + "x",
            source=RouteProvenance.CONTRACT,
            response_shape={"semantic_tags": ["api_read"]},
        )
        src = SymbolSource(
            "contract", RouteProvenance.CONTRACT, [cand], SymbolTable({BASE_SYM: base}),
        )
        q = RouteQuery(asset=AssetRef(id="web-01"), hints=RouteHints(), min_confidence=0.0)
        return Resolver(sources=[src]).resolve(q, now=1000).bindings[0].identity_template

    assert ident("http" + ":" + SEP * 2 + "h") == SEP + "api" + SEP + "x"
    assert ident("http" + ":" + SEP * 2 + "h:80") == SEP + "api" + SEP + "x"


def test_symbol_table_reports_missing() -> None:
    syms = SymbolTable({"A": "x"})
    assert syms.missing("{A}" + SEP + "{B}", known_params=()) == ["B"]
    assert syms.missing("{A}" + SEP + "{p}", known_params=("p",)) == []


def test_trailing_slash_is_stripped_from_symbol_value() -> None:
    """``.../x/`` + ``/api`` 会拼出双斜杠——那是模板拼接最常见的低级错误。"""
    syms = SymbolTable({BASE_SYM: "http" + ":" + SEP * 2 + "h" + SEP})
    src = SymbolSource("contract", RouteProvenance.CONTRACT,
                       [_cand("{" + BASE_SYM + "}" + SEP + "api")], syms)
    assert src.collect(RouteQuery(asset=AssetRef(id="a")))[0].template == (
        "http" + ":" + SEP * 2 + "h" + SEP + "api")


def test_symbol_source_interops_with_static_source() -> None:
    """同一份 routeset 里可以混用符号源与静态源。"""
    syms = SymbolTable({BASE_SYM: "http" + ":" + SEP * 2 + "h"})
    a = SymbolSource("contract", RouteProvenance.CONTRACT,
                     [_cand("{" + BASE_SYM + "}" + SEP + "a" + SEP + "{id}")], syms)
    b = StaticSource("operator", RouteProvenance.OPERATOR,
                     [_cand(SEP + "b" + SEP + "{id}")])
    got = _resolve([a, b])
    assert len(got) == 2
