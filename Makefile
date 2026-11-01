# VeriPen —— 人机共用入口（24 §5 的 make 目标子集，B1 只落必要项）
#
# 一次只跑一批；每批 DoD 全绿即停（21 §4）。

PY ?= python
PKG_DIRS := veripen_core veripen_oracles veripen_contract api worker web tests

.PHONY: help test bindings-check lint-noroute lint-nodata-stub lint b1 b4 b5 b6 b7 b8 b9 b10 b11 b12 b13 b14 b15 b16

help:
	@echo "make test             运行 B1 契约测试"
	@echo "make bindings-check    校验 op_id 绑定覆盖（24 §3）"
	@echo "make lint-noroute      零地址闸门（27 §7）"
	@echo "make lint-nodata-stub  禁用 mock/硬编码数据（26 §5.1）"
	@echo "make lint              上述三道闸门"

test:
	$(PY) -m pytest tests/unit/test_contracts.py -q

bindings-check:
	$(PY) scripts/lint/bindings_check.py $(STRICT)

lint-noroute:
	$(PY) scripts/lint/noroute.py

lint-nodata-stub:
	$(PY) scripts/lint/nodata_stub.py

lint: lint-noroute lint-nodata-stub bindings-check

# B1 退出标准（DoD）一键：测试 + 三道闸门
b1: test lint

# B1.5 路由层
routes-l0:
	$(PY) -m pytest tests/routes/test_l0.py -q

routes-contract:
	$(PY) -m pytest tests/routes -k routes_contract -q

lint-netimports:
	$(PY) scripts/lint/netimports.py

types-check:
	$(PY) -m mypy --strict veripen_core/ worker/ veripen_oracles/

b1.5: routes-l0 routes-contract lint-netimports types-check lint

# B2 调度
schedule-test:
	$(PY) -m pytest tests/unit/test_scheduler.py -q

takeover-test:
	$(PY) -m pytest tests/integration/test_worker_takeover.py -q

b2: schedule-test takeover-test types-check lint

# B3 guard
guard-test:
	$(PY) -m pytest tests/unit/test_guard.py -q

b3: guard-test types-check lint

# B4 证据（capsule 1.1 / 链 / 六态 replay）
CAPSULE ?= out/b4/capsules
evidence-test:
	$(PY) -m pytest tests/unit/test_evidence.py -q

replay-order-test:
	$(PY) -m pytest tests/unit/test_replay_order.py -q

replay-partial-test:
	$(PY) -m pytest tests/unit/test_replay_partial.py -q

evidence-acceptance:
	$(PY) -m pytest tests/integration/test_evidence_acceptance.py -q

# 本地演示链：三个 capsule + verify-chain + 离线 replay（不联网）
evidence-demo:
	$(PY) scripts/b4/demo_chain.py --out $(CAPSULE)

verify-chain:
	$(PY) -m veripen_core.evidence --verify-chain $(CAPSULE)/*.json

verify-capsule:
	$(PY) -m veripen_core.evidence --verify-capsule $(CAPSULE)/*.json

replay-offline:
	$(PY) -m veripen_core.evidence --offline-fixture $(CAPSULE)/*.json

b4: evidence-test replay-order-test replay-partial-test evidence-acceptance evidence-demo verify-chain verify-capsule types-check lint

# B5 TierA×4 + OOB（本地靶场，零 socket）
oracle-test:
	$(PY) -m pytest tests/unit/test_oracle_tierA.py tests/unit/test_oracle_framework.py -q

oob-test:
	$(PY) -m pytest tests/unit/test_oob.py tests/lab/test_listener.py -q

oracle-cases:
	$(PY) -m pytest tests/unit/test_oracle_cases.py -q

# 不碰内核：B5 的硬门（21 §2 B5）
core-untouched:
	@test -z "$$(git diff --stat -- veripen_core/)" || (echo "veripen_core/ 被改动：B5 不许碰内核"; git diff --stat -- veripen_core/; exit 1)

b5: oracle-test oob-test oracle-cases core-untouched types-check lint

# B6 TierA 余 2 类
b6: oracle-test oob-test oracle-cases core-untouched types-check lint

# B7 TierB 5 类（写操作/并发类默认关闭，启动校验必过）
tierb-test:
	$(PY) -m pytest tests/unit/test_oracle_tierB.py -q

# 默认关闭是"启动期抛错"，不是文档里的一句话：这里实际跑一次校验
lint-policy:
	$(PY) scripts/lint/policy_check.py

b7: tierb-test oracle-cases lint-policy core-untouched types-check lint

# B8 契约解析（parser + planner → RouteCandidate；不产地址）
contract-test:
	$(PY) -m pytest tests/fixtures/contract -q

# 55 op 契约 → operator 确认后的绑定数；同输入两次同 routeset_sha256
contract-demo:
	$(PY) scripts/b8/contract_demo.py

b8: contract-test contract-demo core-untouched types-check lint

# B9 认证清单（J1–J10 + O/M/S 检查单 + 覆盖矩阵）
auth-test:
	$(PY) -m pytest tests/unit/test_auth_suite.py -q

auth-matrix:
	$(PY) scripts/b9/auth_matrix.py

b9: auth-test auth-matrix core-untouched types-check lint

# B10 agent 循环（预算硬刹车 / 窄 schema / 降级链）—— 本批起允许改 core
agent-test:
	$(PY) -m pytest tests/unit/test_agent_loop.py -q

b10: agent-test types-check lint

# B11 报告三视图（MD / SARIF / JSON + manifest 防手改）
report-test:
	$(PY) -m pytest tests/unit/test_report.py -q

report-demo:
	$(PY) scripts/b11/build_demo_report.py out/b11/report

# 手工改报告 → manifest 校验必须失败（这里验证"没改"的那一半）
verify-report:
	$(PY) scripts/b11/verify_manifest.py out/b11/report

b11: report-test report-demo verify-report types-check lint

# B12 bench（44 例 × 3 轮；硬门槛 FP=0 / 越界=0 / routeset 三轮一致 / 渲染可由 RouteSet 推出）
bench-test:
	$(PY) -m pytest tests/unit/test_bench.py -q

bench:
	$(PY) scripts/b12/run_bench.py --rounds 3 --out out/b12

b12: bench-test bench types-check lint

# B15 只读控制台（ui.* 写请求 405；绑定页只给摘要）
console-test:
	$(PY) -m pytest tests/unit/test_console.py -q

b15: console-test types-check lint

# B16 双客户档（A1：差异全部在 YAML，内核零改动）
profile-test:
	$(PY) -m pytest tests/unit/test_profiles.py -q

profile-diff:
	$(PY) scripts/b16/profile_diff.py

# 换档不改代码：本批必须让这两个目录的 diff 为空
profiles-only:
	@test -z "$$(git diff --stat -- veripen_core/ veripen_oracles/ | grep -v veripen_core/profile.py)" 		|| (echo "B16 动了内核（profile.py 除外）"; exit 1)

b16: profile-test profile-diff core-untouched types-check lint
