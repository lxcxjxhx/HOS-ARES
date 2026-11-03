# VeriPen —— 人机共用入口（24 §5 的 make 目标子集，B1 只落必要项）
#
# 一次只跑一批；每批 DoD 全绿即停（21 §4）。

PY ?= python
PKG_DIRS := veripen_core veripen_oracles veripen_contract veripen_cli api worker web tests
VERSION ?= 1.0.0

# 离线包（A3「内网能装」）：组包 + 立刻自校验
package-offline:
	$(PY) scripts/package_offline.py --version $(VERSION)

# 部署自检 16 项（skip 不算通过，全 ok 才叫绿）
doctor:
	$(PY) -m veripen_cli.cmd_doctor

.PHONY: agent-discover hunt-creds b29 b34 help test doctor package-offline bindings-check lint-noroute lint-nodata-stub lint-noleak replay coverage-matrix cases bench-leads plugin-roundtrip workbench-smoke assertions b1 b4 b5 b6 b7 b8 b9 b10 b11 b12 b12.1 b12.2 b12.3 b12.4 b12.5 b12.6 b12.7 b12.8 b12.9 b12.10 b12.11 b12.12 b12.13 b12.14 b12.15 b12.16 bench-abi bench-rerun lint-act-cmd lint-ui b13 b14 b15 b16 b17

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
	$(PY) -m mypy --strict veripen_core/ worker/ veripen_oracles/ veripen/ veripen_cli/

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

# ── v3.6 / B18：A2/A4/A6/A7/A10/A11/A12 的可执行断言 ────────────────
# 每一条都必须**能红**（见 39 §3）。红不是缺陷，是缺口可见。

# A2 差异没漏进代码（客户串不许出现在内核）
lint-noleak:
	$(PY) scripts/lint/noleak.py

# A4 不是「我说有洞」：篡改 1 字节必须 TAMPERED（无靶场可跑）
replay:
	@test -n "$(ID)" || (echo "用法：make replay ID=<capsule.json> [MODE=dry]"; exit 2)
	$(PY) -m veripen_cli.cmd_replay --capsule "$(ID)" --mode $(or $(MODE),dry) --out out/replay
	$(PY) -m veripen_cli.cmd_replay --capsule "$(ID)" --selftest

# A6 覆盖够宽：14 类 oracle × 用例，0 用例的一类必须可见
coverage-matrix:
	$(PY) -m veripen_cli.cmd_coverage --out out/coverage

# A7 接口能自动覆盖：契约 → 候选，两次摘要必须一致、候选零地址
cases:
	@test "$(or $(CASES),x)" = "contract" || (echo "用法：make cases CASES=contract"; exit 2)
	$(PY) -m veripen_cli.cmd_cases --contract tests/fixtures/contract/sample.json --check --out out/cases

# A10 能打同时能证明（B19/B21 之前必然红：act.* 未接、闭环率算不出）
bench-leads:
	$(PY) -m veripen_cli.cmd_bench_leads --out out/leads

# A11 能力可扩展（B20 之前必然红：远程 fetch 未接）
plugin-roundtrip:
	$(PY) -m veripen_cli.cmd_plugin_roundtrip --out out/plugins

# A12 工作台（B19 之前必然红：无 Executor 实现）
workbench-smoke:
	$(PY) -m veripen_cli.cmd_workbench_smoke --out out/workbench

# 断言总览：一次跑完 A2/A4/A6/A7/A10/A11/A12（允许部分红，用于看当前缺口）
assertions:
	@echo "== A2 lint-noleak ==";        $(PY) scripts/lint/noleak.py || true
	@echo "== A6 coverage-matrix ==";    $(PY) -m veripen_cli.cmd_coverage --out out/coverage || true
	@echo "== A7 cases ==";              $(PY) -m veripen_cli.cmd_cases --contract tests/fixtures/contract/sample.json --check --out out/cases || true
	@echo "== A10 bench-leads ==";       $(PY) -m veripen_cli.cmd_bench_leads --out out/leads || true
	@echo "== A11 plugin-roundtrip ==";  $(PY) -m veripen_cli.cmd_plugin_roundtrip --out out/plugins || true
	@echo "== A12 workbench-smoke ==";   $(PY) -m veripen_cli.cmd_workbench_smoke --out out/workbench || true

# B12.9 指标重跑：口径变了（error 率 / undetermined / agent_reported=0），
# 且必须证明"补齐不改结论"——前后 finding 集合差为空
bench-rerun:
	$(PY) scripts/b12/run_bench.py --rounds 3 --out out/b12 --diff-findings out/b12/findings_pinned.txt

b12.9: bench-test bench-rerun

b12: bench-test bench types-check lint

# B15 只读控制台（ui.* 写请求 405；绑定页只给摘要）
console-test:
	$(PY) -m pytest tests/unit/test_console.py -q

b15: console-test types-check lint

# B35 展示语义层（H06 §2 / A13 界面不说谎、A14 空值四态）
ui-honesty:
	$(PY) -m pytest tests/unit/test_ui_semantics.py -q -k "honesty"

ui-semantics:
	$(PY) -m pytest tests/unit/test_ui_semantics.py -q -k "semantics"

b35: ui-honesty ui-semantics console-test types-check lint

# B36 页面契约对齐（H06 §4 / A15：payload ⊇ view 键集，缺一即红）
ui-contract:
	$(PY) -m pytest tests/unit/test_ui_contract.py -q

b36: ui-contract console-test types-check lint

# B37 装载器：产物 → 状态（只读、确定性、空输入安全）
ui-serve:
	$(PY) -m veripen_cli.cmd_console --port 8099

ui-loader:
	$(PY) -m pytest tests/unit/test_ui_loader.py -q

b37: ui-loader console-test types-check lint

# B38 概览/摘要卡片 + 覆盖矩阵（H06 §3；A16 的静态结构部分）
ui-dom:
	$(PY) -m pytest tests/unit/test_ui_dom.py -q

b38: ui-dom ui-contract ui-loader types-check lint

# B16 双客户档（A1：差异全部在 YAML，内核零改动）
profile-test:
	$(PY) -m pytest tests/unit/test_profiles.py -q

profile-diff:
	$(PY) scripts/b16/profile_diff.py

# 换档不改代码：本批必须让这两个目录的 diff 为空
profiles-only:
	@test -z "$$(git diff --stat -- veripen_core/ veripen_oracles/ | grep -v veripen_core/profile.py)" 		|| (echo "B16 动了内核（profile.py 除外）"; exit 1)

b16: profile-test profile-diff core-untouched types-check lint

# B17 交付物料（端到端演示 + 四份文档）
demo:
	$(PY) scripts/demo.py --out out/demo

demo-offline:
	$(PY) scripts/demo.py --out out/demo --offline-only

demo-alt:
	$(PY) scripts/demo.py --profile demo-b --alt-profile demo-a --out out/demo-alt

delivery-test:
	$(PY) -m pytest tests/unit/test_delivery_docs.py -q

b17: delivery-test demo demo-alt types-check lint

# ══════════════════════════════════════════════════════════════════
# B12.x 新口径（v3.2–v3.4）：13 个节点，顺序见 21 §W3.6
# ══════════════════════════════════════════════════════════════════

# spec-drift：规划与代码的差异闸门。missing 必须为 0 才能继续下一批
spec-drift:
	$(PY) scripts/lint/spec_drift.py

spec-drift-baseline:
	@mkdir -p out/b12-completion
	$(PY) scripts/lint/spec_drift.py --baseline out/b12-completion/baseline.txt --only-missing

# 17 条一致性自检（03 §7）
selfcheck-test:
	$(PY) -m pytest tests/unit/test_blackboard.py -q

ledger-check:
	$(PY) scripts/lint/ledger_check.py

b12.1: selfcheck-test spec-drift types-check lint

# B12.2 outcome 四态 + 覆盖来源（三条自洽规则：模型 / 落库 / 库触发器各守一次）
outcome-test:
	$(PY) -m pytest tests/unit/test_outcome.py -q

b12.2: outcome-test types-check lint

# B12.3 workflow DAG：18 §0 波次 ↔ 33 §2 九阶段，环检测 + 跳过必落 task_events
workflow-test:
	$(PY) -m pytest tests/unit/test_workflow.py -q

b12.3: workflow-test spec-drift types-check lint

# B12.4 knowledge 层：只进 L2、永不进判定（改知识全文 verdict 逐字不变）
knowledge-test:
	$(PY) -m pytest tests/unit/test_knowledge_redline.py -q

b12.4: knowledge-test types-check lint

# B12.5 多 agent：agent 之间只传任务不传结论（分工不协商）
multi-agent-test:
	$(PY) -m pytest tests/unit/test_multi_agent.py -q

# 生死线：单/多 agent 的 findings 集合与请求数必须逐字相同
bench-abi:
	$(PY) scripts/b12/bench_abi.py

b12.5: multi-agent-test bench-abi types-check lint

# B12.6 自由动作 act.*：六动作 + 白名单二进制 + 地址零入库
actions-test:
	$(PY) -m pytest tests/unit/test_actions.py -q

# argv 模板与动作层源码里不许出现字面地址，也不许 import 判定类型
lint-act-cmd:
	$(PY) scripts/lint/act_cmd.py

b12.6: actions-test lint-act-cmd types-check lint

# B12.7 线索闭环：pending 强制下一步 / 死线索不占预算 / pending>0 不许 DONE
leads-test:
	$(PY) -m pytest tests/unit/test_leads.py -q

b12.7: leads-test types-check lint

# B12.8 操作台 SSE：五类事件 / act 必带 sandbox_id / 控制台改不了 verdict
stream-test:
	$(PY) -m pytest tests/unit/test_stream.py -q

lint-ui:
	$(PY) scripts/lint/ui_zero_address.py

b12.8: stream-test lint-ui types-check lint

# B12.10 插件发现层：score 可复算 + 6h 内刷新 0 请求
plugin-discovery-test:
	$(PY) -m pytest tests/unit/test_plugin_discovery.py -q

b12.10: plugin-discovery-test types-check lint

# B12.11 插件注册层：lock 可回滚 / 6h 冷却 / 一次刷新 ≤2 请求 / 上游消失不自动卸载
plugin-registry-test:
	$(PY) -m pytest tests/unit/test_plugin_registry.py -q

b12.11: plugin-registry-test types-check lint

# B12.12 装配与准入：四挂载点只有 oracle 能判定 / oracle 必过 44 例 / 插件直连被拦
plugin-admission-test:
	$(PY) -m pytest tests/unit/test_plugin_admission.py -q

b12.12: plugin-admission-test types-check lint

# B12.13 插件 UI 渲染契约：四色标 + 缓存时间可见 + 版本箭头（不碰 HTML）
plugin-view-test:
	$(PY) -m pytest tests/unit/test_plugin_view.py -q

b12.13: plugin-view-test lint-ui types-check lint

# B12.14 工作台读写层：绝对路径拒 / write 不覆盖 / edit 次数不符整体失败 / 无 delete
workbench-test:
	$(PY) -m pytest tests/unit/test_workbench_fs.py -q

b12.14: workbench-test types-check lint

# B12.15 shell.run + 强制代理：五道闸 / 脚本含 URL 即拒 / 直连失败走代理成功
shell-test:
	\ -m pytest tests/integration/test_shell_proxy.py -q

b12.15: shell-test types-check lint

# B12.16 计划与任务层：plan.enter 锁写 / exit 未确认不放行 / todo 与 leads 一致
plan-test:
	\ -m pytest tests/unit/test_plan_mode.py -q

b12.16: plan-test types-check lint

# ══════════════════════════════════════════════════════════════════
# v3.7 / 40 验证批次：判定入口（与实现批次的 make 目标分开）
# ══════════════════════════════════════════════════════════════════

# B29 模型发现能力：拿掉「pending 非空就 run_oracle」，测模型会不会自己验。
# **模型未配置时 SKIP，不算通过也不算失败**——没测过的东西不许记成测过（D3）。
agent-discover:
	$(PY) scripts/b26/agent_live.py --discover --no-fallback --out out/b29 		|| echo "SKIP: 模型未配置（需要 VERIPEN_MODEL_BASE_URL / VERIPEN_MODEL / VERIPEN_MODEL_API_KEY）"

# 同一份用例带上 scripted 兜底：用来区分「模型验的」与「降级后验的」
agent-discover-fallback:
	$(PY) scripts/b26/agent_live.py --discover --out out/b29 		|| echo "SKIP: 模型未配置"

# B34 凭据不出现在命令行、告警与产物里
hunt-creds:
	$(PY) -m pytest tests/integration/test_hunt_credentials.py -q

b29: agent-discover
b34: hunt-creds types-check lint

