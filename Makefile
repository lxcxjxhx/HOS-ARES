# HOS-ARES · Makefile
# 每个目标 = 一条命令 + 一句断言（`24 §5`）。25 个目标随批次逐步落地，未实现的显式报错。

PY ?= python
export PYTHONPATH := $(CURDIR)

.DEFAULT_GOAL := help

.PHONY: help test test-fast test-unit test-e2e \
        lint-noleak lint-nodata-stub lint lint-report \
        up init db-shell migrate \
        batch-record batch-status \
        clean

help: ## 列出所有目标
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'

# ---------- 测试 ----------

test: lint-nodata-stub lint-noleak test-unit ## 全量测试 + 两道 lint

test-fast: test-unit ## 只跑单测

test-unit: ## 单元测试（不需要靶场）
	$(PY) -m pytest tests/unit -q

test-e2e: ## 端到端（需要 docker compose up）
	$(PY) -m pytest tests/e2e -q

# ---------- lint ----------

lint: lint-noleak lint-nodata-stub ## 全部机械检查

lint-noleak: ## A2：core/oracles 无客户标识
	$(PY) scripts/lint_noleak.py

lint-nodata-stub: ## 26：禁 mock 数据
	$(PY) scripts/lint_nodata_stub.py

lint-report: ## B11：报告措辞与同源校验（未实现）
	@echo "lint-report: not implemented until B11 (docs/plan/21 §2)"; exit 1

# ---------- 运行时 ----------

up: ## 起全栈（B13 起可用）
	@echo "up: not implemented until B13"; exit 1

db-shell: ## 打开 SQLite
	$(PY) -c "import sqlite3,sys; c=sqlite3.connect('var/veripen.db'); c.interactive()"

migrate: ## 应用迁移（只前进）
	$(PY) -c "from veripen_core.store import Store; Store('var/veripen.db'); print('migrations applied')"

# ---------- 批次账本 ----------

batch-record: ## 追加一行批次记录：make batch-record BATCH=B1 STATE=done
	@test -n "$(BATCH)" || (echo "usage: make batch-record BATCH=B1 STATE=done"; exit 1)
	$(PY) scripts/batch_ledger.py append --batch $(BATCH) --state $(or $(STATE),done)

batch-status: ## 显示 17 批进度
	$(PY) scripts/batch_ledger.py status

clean: ## 清本地产物（不动 .veripen 账本）
	rm -rf .pytest_cache __pycache__ */__pycache__ */*/__pycache__ out
