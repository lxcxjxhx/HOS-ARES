# VeriPen —— 人机共用入口（24 §5 的 make 目标子集，B1 只落必要项）
#
# 一次只跑一批；每批 DoD 全绿即停（21 §4）。

PY ?= python
PKG_DIRS := veripen_core veripen_oracles veripen_contract api worker web tests

.PHONY: help test bindings-check lint-noroute lint-nodata-stub lint b1

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
	$(PY) -m mypy --strict veripen_core/

b1.5: routes-l0 routes-contract lint-netimports types-check lint
