"""guard 测试（B3 DoD：缩 scope 后拒绝且零请求；令牌桶 100 并发≈20/s；越界记 scope ok=0）。"""

from __future__ import annotations

import threading
import time

import pytest

from veripen_core import (
    Budget,
    ImpactClass,
    Store,
    Task,
    TaskEvent,
    TaskState,
    idem_key,
    new_ulid,
)
from veripen_core.errors import ERR_DENY_HIT, ERR_IMPACT_EXCEEDED, ScopeDenied, VeripenError
from veripen_core.guard import (
    DEFAULT_DENY_NETS,
    ApprovalGate,
    ApprovalGuard,
    ApprovalOutcome,
    AssetSpec,
    CheckPoint,
    GateWhen,
    LocalTokenBucket,
    RateLimiter,
    ScopeGuard,
    ScopePolicy,
    TargetBackoff,
    is_sensitive,
    normalize_ip,
    parse_target_url,
    redact_headers,
    header_name_hashes,
)

# ---------- 夹具 ----------


def _policy(**over) -> ScopePolicy:
    base = dict(
        engagement_id="ENG-1",
        assets=(
            AssetSpec(id="web-01", kind="url", value="http://dvwa.local"),
            AssetSpec(id="web-02", kind="url", value="http://juice.local"),
        ),
        enabled_oracles=("sqli_bool_diff", "idor_cross_account"),
    )
    base.update(over)
    return ScopePolicy(**base)


def _resolver(table: dict[str, list[str]]):
    def _r(host: str) -> list[str]:
        if host not in table:
            raise OSError(f"NXDOMAIN {host}")
        return table[host]

    return _r


# ---------- DoD 1：缩 scope 后拒绝且零请求 ----------


def test_scope_shrink_denies_and_sends_zero_requests() -> None:
    """缩小 scope 后必须拒绝，且**一个请求都不发**。

    这里用"计数器 + 假的发送函数"证明零请求：
    只要 guard 抛了，发送函数就不该被调用。
    """
    sent: list[str] = []

    def fake_send(url: str) -> None:
        sent.append(url)

    # 原本 profile 只授权 web-01；请求 web-02（缩小后的 scope 之外）
    guard = ScopeGuard(_policy(assets=(AssetSpec(id="web-01", kind="url", value="http://dvwa.local"),)))

    with pytest.raises(ScopeDenied) as exc:
        guard.check_plan(asset_id="web-02", oracle_id="sqli_bool_diff", impact=ImpactClass.READ_ONLY)
        fake_send("http://juice.local")  # 不该执行到这里

    assert exc.value.code == "VP-2001"
    assert sent == [], f"zero requests must be sent, got {sent}"


def test_scope_allows_authorized_asset() -> None:
    guard = ScopeGuard(_policy())
    guard.check_plan(asset_id="web-01", oracle_id="sqli_bool_diff", impact=ImpactClass.READ_ONLY)  # 不抛


def test_disabled_oracle_denied() -> None:
    """oracle 不在 enabled 里 → BLOCKED 不发包（`05 §1` ①）。"""
    guard = ScopeGuard(_policy())
    with pytest.raises(ScopeDenied):
        guard.check_plan(asset_id="web-01", oracle_id="mass_assignment", impact=ImpactClass.READ_ONLY)


def test_impact_exceeding_max_denied() -> None:
    guard = ScopeGuard(_policy())
    with pytest.raises(ScopeDenied) as exc:
        guard.check_plan(asset_id="web-01", oracle_id="sqli_bool_diff", impact=ImpactClass.REVERSIBLE_WRITE)
    assert exc.value.code == ERR_IMPACT_EXCEEDED


def test_asset_in_deny_net_denied() -> None:
    """资产本身落在 deny 网段 → 启动即拒（`02 §3` 语义校验）。"""
    guard = ScopeGuard(
        _policy(assets=(AssetSpec(id="meta", kind="url", value="http://169.254.169.254/latest/meta-data"),))
    )
    with pytest.raises(ScopeDenied) as exc:
        guard.check_plan(asset_id="meta", oracle_id="sqli_bool_diff", impact=ImpactClass.READ_ONLY)
    assert exc.value.code == ERR_DENY_HIT


# ---------- DoD 3：越界记 scope ok=0 ----------


def test_violation_records_scope_event_ok_false() -> None:
    """越界必须记 `kind=scope, ok=False, detail.VIOLATION`（`05 §1` ③ + `05 §6`）。"""
    store = Store(":memory:")
    try:
        ts = 1_700_000_000
        task = Task(
            id=new_ulid(),
            engagement_id="ENG-1",
            idem_key=idem_key(engagement_id="ENG-1", asset_id="web-01", oracle_id="sqli_bool_diff"),
            profile_name="demo-a",
            profile_hash="sha256:" + "a" * 64,
            asset_id="web-01",
            oracle_ids=["sqli_bool_diff"],
            budget=Budget(),
            created_at=ts,
            updated_at=ts,
        )
        assert store.insert_task(task)

        guard = ScopeGuard(_policy())
        guard.resolve_once(guard.policy.asset("web-01"), resolver=_resolver({"dvwa.local": ["93.184.216.34"]}))  # type: ignore[arg-type]

        try:
            guard.check_post_connect(peer_ip="10.1.2.3", asset_id="web-01", url="http://dvwa.local/")
        except ScopeDenied as exc:
            store.append_event(
                TaskEvent(
                    task_id=task.id,
                    ts=ts,
                    kind="scope",
                    ok=False,
                    detail={"VIOLATION": "peer_in_deny", **exc.detail},
                )
            )
        else:
            pytest.fail("post_connect must reject a deny-listed peer")

        # 机械验证：库里确实有 scope ok=0 行
        rows = store.events(task.id)
        assert len(rows) == 1
        assert rows[0]["kind"] == "scope"
        assert rows[0]["ok"] == 0
        assert "VIOLATION" in rows[0]["detail_json"]

        # doctor 的第五条第 5 项必须非 0（出现即事故）
        check = store.self_check()
        assert check["scope_violations"] == 1
    finally:
        store.close()


# ---------- 三处校验各自的行为 ----------


def test_post_connect_rejects_unresolved_asset_under_strict() -> None:
    """strict dns_policy 下未预解析 → fail-closed（不许"没解析过所以放行"）。"""
    guard = ScopeGuard(_policy())
    with pytest.raises(ScopeDenied):
        guard.check_post_connect(peer_ip="93.184.216.34", asset_id="web-01", url="http://dvwa.local/")


def test_post_connect_accepts_resolved_peer() -> None:
    guard = ScopeGuard(_policy())
    guard.resolve_once(guard.policy.asset("web-01"), resolver=_resolver({"dvwa.local": ["93.184.216.34"]}))  # type: ignore[arg-type]
    guard.check_post_connect(peer_ip="93.184.216.34", asset_id="web-01", url="http://dvwa.local/")  # 不抛


def test_post_connect_rejects_peer_outside_resolved_set() -> None:
    """DNS rebinding 场景：对端不是预解析那个 IP → 断连（`05 §1` ③）。"""
    guard = ScopeGuard(_policy())
    guard.resolve_once(guard.policy.asset("web-01"), resolver=_resolver({"dvwa.local": ["93.184.216.34"]}))  # type: ignore[arg-type]
    with pytest.raises(ScopeDenied) as exc:
        guard.check_post_connect(peer_ip="93.184.216.99", asset_id="web-01", url="http://dvwa.local/")
    assert exc.value.detail["VIOLATION"] == "peer_not_in_scope"


def test_redirect_denied_when_max_redirects_zero() -> None:
    """`max_redirects: 0` 时第一次跳转就拒（`05 §1.1` 302 越界）。"""
    guard = ScopeGuard(_policy(max_redirects=0))
    with pytest.raises(ScopeDenied) as exc:
        guard.check_redirect(hop=1, target_url="http://evil.example.com/", asset_id="web-01")
    assert exc.value.code == "VP-2003"


def test_url_with_userinfo_rejected() -> None:
    """`http://allowed@evil/` 这类混淆直接拒（`05 §1.1`）。"""
    with pytest.raises(ScopeDenied):
        parse_target_url("http://dvwa.local@evil.example.com/")


def test_ipv4_mapped_ipv6_normalized() -> None:
    """IPv4-mapped IPv6 归一化后再判（`05 §1.1`）。"""
    assert str(normalize_ip("::ffff:127.0.0.1")) == "127.0.0.1"
    assert str(normalize_ip("2130706433")) == "127.0.0.1"


def test_deny_nets_include_metadata_and_private() -> None:
    """云元数据 + 私网必须在默认 deny 内（`05 §1.1`）。"""
    nets = " ".join(DEFAULT_DENY_NETS)
    for must in ("169.254.169.254/32", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "::1/128"):
        assert must in nets


# ---------- DoD 2：令牌桶 100 并发 ≈ 20/s ----------


def test_token_bucket_100_concurrent_is_about_20_per_second() -> None:
    """100 并发请求在 ~20 QPS、burst 40 下应耗时约 3 秒（40 立即 + 60/20）。

    允许 ±50% 容差（调度抖动）。核心断言是"总耗时落在量级内"，
    而不是被 burst 一次放完（那会是 ~0s），也不是被限到 1 QPS（那会是 ~100s）。
    """
    bucket = LocalTokenBucket(rate=20.0, burst=40)
    granted = 0
    lock = threading.Lock()

    def worker() -> None:
        nonlocal granted
        # 允许最多等 10 秒，够 100 个跑完
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if bucket.try_acquire():
                with lock:
                    granted += 1
                return
            time.sleep(0.005)

    start = time.monotonic()
    threads = [threading.Thread(target=worker) for _ in range(100)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    elapsed = time.monotonic() - start

    assert granted == 100, f"all requests must eventually be granted, got {granted}"
    # 40 burst 立即 + 60 个以 20/s 渐进 ≈ 3.0s；容许 1.5s..6s
    assert 1.5 <= elapsed <= 6.0, f"100 requests took {elapsed:.2f}s, expected ~3s at 20QPS burst 40"


def test_token_bucket_respects_burst() -> None:
    """burst 内立即放行（40 个），第 41 个立刻被拒。"""
    bucket = LocalTokenBucket(rate=20.0, burst=40, clock=lambda: 0.0)
    assert all(bucket.try_acquire() for _ in range(40))
    assert bucket.try_acquire() is False


def test_rate_limiter_queues_then_times_out() -> None:
    """拿不到令牌时排队；超 `max_wait_s` 抛 VP-3003（不丢任务，`04 §5`）。"""
    bucket = LocalTokenBucket(rate=0.001, burst=1, clock=lambda: 0.0)
    limiter = RateLimiter(bucket=bucket, max_wait_s=0.2, retry_sleep_s=0.01)

    # 第一次拿到 burst
    assert limiter.acquire(asset_id="web-01").granted is True

    with pytest.raises(VeripenError) as exc:
        limiter.acquire(asset_id="web-01")
    assert exc.value.code == "VP-3003"


def test_asset_semaphore_limits_concurrency() -> None:
    """每资产并发 2（`04 §5` 第 2 层）。"""
    limiter = RateLimiter(bucket=LocalTokenBucket(rate=1000, burst=1000), per_asset_concurrency=2)
    sem = limiter.asset_slot("web-01")
    sem.acquire()
    sem.acquire()
    assert sem.acquire(blocking=False) is False, "third acquire must block when limit is 2"
    sem.release()
    assert sem.acquire(blocking=False) is True


def test_target_backoff_on_429() -> None:
    """目标 429 → 降速 30%，持续 60s（`04 §5` 第 3 层）。"""
    tb = TargetBackoff(base_rate=20.0)
    assert tb.effective_rate(now=0.0) == 20.0
    tb.observe_429(now=0.0)
    assert tb.effective_rate(now=10.0) == pytest.approx(14.0)
    assert tb.effective_rate(now=61.0) == pytest.approx(20.0)


def test_target_circuit_opens_after_five_5xx() -> None:
    """连续 5 次 5xx → 该资产熔断 5 分钟（`04 §5`）。"""
    tb = TargetBackoff()
    for _ in range(4):
        tb.observe_status("web-01", 502, now=0.0)
    assert tb.circuit_open("web-01", now=0.0) is False
    tb.observe_status("web-01", 502, now=0.0)
    assert tb.circuit_open("web-01", now=1.0) is True
    assert tb.circuit_open("web-01", now=301.0) is False


# ---------- 审批门 ----------


def _task(store: Store, *, state: TaskState = TaskState.RUNNING, lease_owner: str = "worker-a") -> Task:
    ts = 1_700_000_000
    t = Task(
        id=new_ulid(),
        engagement_id="ENG-1",
        idem_key=idem_key(engagement_id="ENG-1", asset_id="web-01", oracle_id=f"o{new_ulid()[-6:]}"),
        profile_name="demo-a",
        profile_hash="sha256:" + "a" * 64,
        asset_id="web-01",
        oracle_ids=["sqli_bool_diff"],
        state=TaskState.QUEUED,
        budget=Budget(),
        created_at=ts,
        updated_at=ts,
    )
    assert store.insert_task(t)
    store.transition(t.id, TaskState.QUEUED, TaskState.LEASED, now=ts, lease_owner=lease_owner)
    store.transition(t.id, TaskState.LEASED, TaskState.RUNNING, now=ts)
    got = store.get_task(t.id)
    assert got is not None
    return got


def test_no_gate_means_denied_by_default() -> None:
    """默认全拒：profile 没写就是不允许（`05 §3`）。"""
    store = Store(":memory:")
    try:
        task = _task(store)
        guard = ApprovalGuard(store=store, gates=())
        res = guard.require(task=task, action_class="reversible_write")
        assert res.outcome is ApprovalOutcome.DENIED
        assert "denied by default" in res.reason
    finally:
        store.close()


def test_gate_creates_pending_and_moves_task() -> None:
    store = Store(":memory:")
    try:
        task = _task(store)
        guard = ApprovalGuard(store=store, gates=(ApprovalGate(action_class="reversible_write"),))
        res = guard.require(task=task, action_class="reversible_write", impact={"why": "demo"})
        assert res.outcome is ApprovalOutcome.PENDING
        assert res.approval_id

        after = store.get_task(task.id)
        assert after is not None
        assert after.state is TaskState.AWAITING_APPROVAL
    finally:
        store.close()


def test_self_approval_forbidden() -> None:
    """`decided_by` 不得等于 `lease_owner`（`05 §3` 写死校验）。"""
    store = Store(":memory:")
    try:
        task = _task(store, lease_owner="worker-a")
        guard = ApprovalGuard(store=store, gates=(ApprovalGate(action_class="reversible_write"),))
        res = guard.require(task=task, action_class="reversible_write")
        assert res.approval_id

        with pytest.raises(VeripenError) as exc:
            guard.decide(approval_id=res.approval_id, decision="approved", by="worker-a", reason="self")
        assert exc.value.code == "VP-2005"

        # 别人批就行
        state = guard.decide(approval_id=res.approval_id, decision="approved", by="secops-lead")
        assert state.value == "APPROVED"
        after = store.get_task(task.id)
        assert after is not None
        assert after.state is TaskState.RUNNING
    finally:
        store.close()


def test_rejected_approval_resumes_task() -> None:
    """REJECTED → 该动作 BLOCKED，任务继续走其余 oracle（`05 §3`）。"""
    store = Store(":memory:")
    try:
        task = _task(store)
        guard = ApprovalGuard(store=store, gates=(ApprovalGate(action_class="scope_expand"),))
        res = guard.require(task=task, action_class="scope_expand")
        assert res.approval_id
        guard.decide(approval_id=res.approval_id, decision="rejected", by="secops-lead", reason="too broad")
        after = store.get_task(task.id)
        assert after is not None
        assert after.state is TaskState.RUNNING, "task must resume so other oracles can run"
    finally:
        store.close()


def test_first_time_gate_allows_after_first_approval() -> None:
    store = Store(":memory:")
    try:
        t1 = _task(store)
        guard = ApprovalGuard(
            store=store, gates=(ApprovalGate(action_class="scope_expand", when=GateWhen.FIRST_TIME),)
        )
        res = guard.require(task=t1, action_class="scope_expand")
        assert res.outcome is ApprovalOutcome.PENDING
        guard.decide(approval_id=res.approval_id, decision="approved", by="lead")

        t2 = _task(store)
        res2 = guard.require(task=t2, action_class="scope_expand")
        assert res2.outcome is ApprovalOutcome.ALLOWED
    finally:
        store.close()


def test_approval_ttl_expiry() -> None:
    """超时 PENDING 转 EXPIRED（`05 §3` approval_ttl=3600s）。"""
    store = Store(":memory:")
    try:
        task = _task(store)
        now = [1_700_000_000.0]
        guard = ApprovalGuard(
            store=store,
            gates=(ApprovalGate(action_class="reversible_write"),),
            ttl_s=3600,
            clock=lambda: now[0],
        )
        res = guard.require(task=task, action_class="reversible_write")
        assert res.approval_id

        # 未超时：不该过期
        assert guard.expire_stale() == []

        # 推进 3601 秒：必须过期
        now[0] += 3601
        expired = guard.expire_stale()
        assert res.approval_id in expired, f"approval must expire after ttl, got {expired}"
        assert guard.pending(task_id=task.id) == []
    finally:
        store.close()


# ---------- 凭据与脱敏 ----------


def test_sensitive_headers_redacted() -> None:
    """`authorization`/`cookie`/`x-api-key` 等一律 `<redacted>`（`05 §4`）。"""
    headers = {
        "Authorization": "Bearer sk-secret",
        "Cookie": "session=abc",
        "X-Api-Key": "k",
        "Accept": "application/json",
    }
    out = redact_headers(headers)
    assert out["Authorization"] == "<redacted>"
    assert out["Cookie"] == "<redacted>"
    assert out["X-Api-Key"] == "<redacted>"
    assert out["Accept"] == "application/json", "non-sensitive headers must survive"


def test_capsule_stores_only_hash_of_sensitive_values() -> None:
    """capsule 只存 header 名与哈希，不存值（`05 §4`）。"""
    headers = {"Authorization": "Bearer sk-secret", "Accept": "text/html"}
    hashed = header_name_hashes(headers)
    assert "authorization" in hashed
    assert hashed["authorization"].startswith("sha256:")
    assert "sk-secret" not in hashed["authorization"]
    assert "accept" not in hashed, "non-sensitive headers are not hashed"


def test_is_sensitive_is_case_insensitive() -> None:
    assert is_sensitive("AUTHORIZATION")
    assert is_sensitive("Set-Cookie")
    assert not is_sensitive("User-Agent")


def test_redaction_filter_scrubs_log_args_dict() -> None:
    """脱敏在 logger 层做，不靠调用方自觉（`05 §4`）。

    注意：`logging.LogRecord` 会把单元素 Mapping 的 args 解包成 dict 本身，
    所以断言要看 `rec.args["Authorization"]` 而不是 `rec.args[0][...]`。
    """
    import logging

    from veripen_core.guard import RedactionFilter

    filt = RedactionFilter()
    rec = logging.LogRecord(
        name="t", level=logging.INFO, pathname="", lineno=1,
        msg="hdr=%(Authorization)s", args=({"Authorization": "Bearer leak"},), exc_info=None,
    )
    assert rec.args == {"Authorization": "Bearer leak"}
    filt.filter(rec)
    assert rec.args["Authorization"] == "<redacted>"
    assert "leak" not in str(rec.args)
