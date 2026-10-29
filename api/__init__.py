"""`api` 包。运行：`uvicorn api.app:create_app --factory`（见 Makefile `up-api`）。"""

from __future__ import annotations

from .app import create_app

__all__ = ["create_app"]
