"""B4 测试夹具：从录制模板构造 routeset 与 capsule（不含任何地址字面量）。

模板一律从 ``tests/routes/fixtures/templates.json`` 读——产品与测试代码里都不出现路由文本
（noroute 的 R-PATH 规则），夹具目录是唯一豁免区。
"""

from __future__ import annotations

import json
from pathlib import Path

from veripen_core.evidence import (
    AssertionBlock,
    CapsuleDoc,
    Chain,
    ClockBlock,
    EnvBlock,
    OracleBlock,
    RouteBlock,
    TargetBlock,
    build_request,
    build_response,
    build_step,
    chain_capsules,
    seal_capsule_doc,
)
from veripen_core.models import Verdict
from veripen_core.routes import (
    AssetRef,
    Resolver,
    RouteHints,
    RouteProvenance,
    RouteQuery,
    StaticSource,
    build_binding,
)
from veripen_core.routes.types import RouteCandidate, RouteSet

ROOT = Path(__file__).resolve().parents[2]
TEMPLATES: dict[str, str] = json.loads(
    (ROOT / "tests" / "routes" / "fixtures" / "templates.json").read_text(encoding="utf-8")
)
NOW = 1_000
CREATED = "2026-11-01T02:14:07Z"
RESOLVED = "2026-11-01T02:13:58Z"
ASSET = "web-01"
PROFILE_HASH = "h-7ce4"


def routeset(template_key: str = "user_by_id", source: RouteProvenance = RouteProvenance.CONTRACT) -> RouteSet:
    cand = RouteCandidate(
        method="GET",
        template=TEMPLATES[template_key],
        param_names=("id",),
        source=source,
    )
    q = RouteQuery(asset=AssetRef(id=ASSET), hints=RouteHints(semantic_tag="object_read"))
    return Resolver(sources=[StaticSource(source.value, source, [cand])]).resolve(q, now=NOW)


def first_ref(rs: RouteSet) -> str:
    return rs.bindings[0].ref


def clone_with_template(rs: RouteSet, ref: str, template: str, template_sha256: str) -> RouteSet:
    """同 ref、不同模板哈希：operator 稳定 ref 场景下的"目标改版"。

    resolver 自己产出的 ref 由 structural_key 决定，模板变了 ref 也会变（那是 UNRESOLVED 场景）；
    这里模拟"绑定身份稳定、但背后的面换了"——正是 ROUTE_DRIFT 要抓的那个。
    """
    old = next(b for b in rs.bindings if b.ref == ref)
    new = build_binding(
        ref=old.ref,
        source=old.source,
        sources_all=old.sources_all,
        confidence=old.confidence,
        template=template,
        method=old.method,
        param_names=old.param_names,
        auth_requirement=old.auth_requirement,
        body_shape_sha256=old.body_shape_sha256,
        structural_key=old.structural_key,
        template_sha256=template_sha256,
        ttl_s=old.ttl_s,
        verifications=old.verifications,
        usable=old.usable,
    )
    return RouteSet(
        resolver_version=rs.resolver_version,
        asset_id=rs.asset_id,
        query_hash=rs.query_hash,
        created_at=rs.created_at,
        expires_at=rs.expires_at,
        sources_used=rs.sources_used,
        sources_failed=rs.sources_failed,
        unresolved=rs.unresolved,
        bindings=[new],
        routeset_sha256=rs.routeset_sha256,
    )


def drop_binding(rs: RouteSet) -> RouteSet:
    return RouteSet(
        resolver_version=rs.resolver_version,
        asset_id=rs.asset_id,
        query_hash=rs.query_hash,
        created_at=rs.created_at,
        expires_at=rs.expires_at,
        sources_used=rs.sources_used,
        sources_failed=rs.sources_failed,
        unresolved=rs.unresolved,
        bindings=[],
        routeset_sha256=rs.routeset_sha256,
    )


def capsule(
    rs: RouteSet | None = None,
    *,
    verdict: Verdict = Verdict.VERIFIED,
    control_status: int = 403,
    probe_body: str = "pair-body",
    baseline_body: str | None = None,
    with_control: bool = True,
) -> CapsuleDoc:
    """合法的 capsule 1.1：baseline/probe/control 三条件齐备。"""
    baseline_body = probe_body if baseline_body is None else baseline_body
    rs = rs or routeset()
    ref = first_ref(rs)
    binding = rs.bindings[0]

    steps = [
        build_step(
            n=1,
            role="baseline",
            account="primary",
            route_ref=ref,
            request=build_request(
                method="GET",
                path_rendered=binding.template.replace("{id}", "41"),
                headers={"Cookie": "session=super-secret", "Accept": "application/json"},
            ),
            response=build_response(status=200, body=baseline_body),
            peer_ip="203.0.113.10",
            elapsed_ms=37,
        ),
        build_step(
            n=2,
            role="probe",
            account="secondary",
            route_ref=ref,
            request=build_request(
                method="GET",
                path_rendered=binding.template.replace("{id}", "41"),
                headers={"Cookie": "session=other-secret"},
            ),
            response=build_response(status=200, body=probe_body),
            peer_ip="203.0.113.10",
            elapsed_ms=41,
        ),
    ]
    if with_control:
        steps.append(
            build_step(
                n=3,
                role="control",
                account="secondary",
                route_ref=ref,
                request=build_request(
                    method="GET",
                    path_rendered=binding.template.replace("{id}", "42"),
                    headers={"Cookie": "session=other-secret"},
                ),
                response=build_response(status=control_status, body="denied" if control_status >= 400 else "leak"),
                peer_ip="203.0.113.10",
                elapsed_ms=33,
                note="对照：访问自己无权拥有的资源应被拒绝",
            )
        )

    return CapsuleDoc(
        finding_id="01JAB0000000000000000000",
        created_at=CREATED,
        oracle=OracleBlock(id="idor_cross_account", version="1.0.0", cwe="CWE-639", params_hash="a" * 64),
        route=RouteBlock(
            routeset_sha256=rs.routeset_sha256,
            ref=ref,
            source=binding.source,
            sources_all=list(binding.sources_all),
            confidence=binding.confidence,
            template_sha256=binding.template_sha256,
            resolver_version=rs.resolver_version,
            resolved_at=RESOLVED,
            ttl_s=binding.ttl_s,
            discovery_evidence_ids=["ev_5c1a", "ev_9d33"],
        ),
        target=TargetBlock(
            asset_id=ASSET,
            frame_method="GET",
            host_key="host_a",
            resolved_ip="203.0.113.10",
            template_sha256=binding.template_sha256,
            userinfo_rejected=True,
        ),
        env=EnvBlock(
            profile_name="demo-a",
            profile_hash=PROFILE_HASH,
            image_digest="dd11",
            runner_id="worker-2@host-abc",
            clock=ClockBlock(t0=1761966847, t1=1761966849),
        ),
        steps=steps,
        assertion=AssertionBlock(
            kind="cross_account",
            statement="secondary 账号读取 primary 拥有资源成功(200)，且对照资源被拒(403)",
            expected={"probe_status": [200, 204], "control_status": [401, 403]},
            observed={"probe_status": 200, "control_status": 403},
            fingerprint={"probe_body_eq_baseline": True, "control_denied": True},
        ),
        verdict=verdict,
        confidence=0.95,
        confidence_rule="double_condition_satisfied",
        route_strength="contract",
        chain=Chain(prev_sha256=None),
    )


def sealed(**kw: object) -> CapsuleDoc:
    return seal_capsule_doc(capsule(**kw))  # type: ignore[arg-type]
