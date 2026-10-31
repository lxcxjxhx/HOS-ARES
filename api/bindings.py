"""绑定加载与解析（24 §3）。

规则：
- 每个 ``op_id`` 必须有绑定（缺一个即启动失败 / bindings-check 失败）。
- ``path_key`` 非空；真正的路径值只从环境变量取，代码与配置文件里都没有。
- 取不到值即 ``BindingsUnresolved``（fail-closed，不回退到任何内置路径）。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import yaml

_ALLOWED_TRANSPORTS = frozenset({"http"})


class BindingsError(RuntimeError):
    """绑定表本身不合法（结构错误）。"""


class BindingsUnresolved(BindingsError):
    """绑定存在但环境未给出路径值（fail-closed）。"""


@dataclass(frozen=True)
class Binding:
    op_id: str
    transport: str
    method: str
    path_key: str

    def resolve_path(self, env: Mapping[str, str] | None = None) -> str:
        source = os.environ if env is None else env
        value = source.get(self.path_key, "").strip()
        if not value:
            raise BindingsUnresolved(
                f"op {self.op_id} 的 path_key {self.path_key} 在环境中无值"
            )
        return value


@dataclass(frozen=True)
class BindingTable:
    api_version: str
    bindings: dict[str, Binding]

    def get(self, op_id: str) -> Binding:
        try:
            return self.bindings[op_id]
        except KeyError as exc:
            raise BindingsError(f"op {op_id} 未登记绑定") from exc

    def resolve_all(self, env: Mapping[str, str] | None = None) -> dict[str, str]:
        return {op: b.resolve_path(env) for op, b in self.bindings.items()}


def load_table(path: str | Path) -> BindingTable:
    raw: Any = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise BindingsError("绑定文件顶层必须是映射")
    ops = raw.get("ops")
    if not isinstance(ops, dict) or not ops:
        raise BindingsError("绑定文件缺少 ops")

    bindings: dict[str, Binding] = {}
    for op_id, spec in ops.items():
        if not isinstance(spec, dict):
            raise BindingsError(f"op {op_id} 的绑定必须是映射")
        transport = str(spec.get("transport", ""))
        if transport not in _ALLOWED_TRANSPORTS:
            raise BindingsError(f"op {op_id} 的 transport 不受支持: {transport!r}")
        method = str(spec.get("method", "")).upper()
        if not method:
            raise BindingsError(f"op {op_id} 缺少 method")
        path_key = str(spec.get("path_key", "")).strip()
        if not path_key:
            raise BindingsError(f"op {op_id} 缺少 path_key")
        bindings[str(op_id)] = Binding(str(op_id), transport, method, path_key)

    return BindingTable(api_version=str(raw.get("api_version", "")), bindings=bindings)


def check_coverage(table: BindingTable, required: tuple[str, ...]) -> list[str]:
    """返回问题清单（空 = 通过）。"""
    problems: list[str] = []
    missing = [op for op in required if op not in table.bindings]
    if missing:
        problems.append("未登记绑定: " + ", ".join(sorted(missing)))
    extra = sorted(set(table.bindings) - set(required))
    if extra:
        problems.append("绑定了未知 op: " + ", ".join(extra))
    return problems


def unresolved_keys(table: BindingTable, env: Mapping[str, str] | None = None) -> list[str]:
    source = os.environ if env is None else env
    return sorted(
        key
        for key in {b.path_key for b in table.bindings.values()}
        if not source.get(key, "").strip()
    )
