"""一致性自检的**可执行版本**（03 §7 的 17 条）。

为什么要有这个模块：`03 §7` 的 17 条 SQL 如果只写在文档里，
就是"规划有、代码无"的另一种形态——和 10 张表只建 3 张是同一个病。

每条自检返回 ``CheckOutcome``：通过时为空，失败时**带上违规行 id**，
这样 doctor 能说"第 3 条不过，是这几条 finding"而不是只报一句 FAIL。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from .store import Store


@dataclass(frozen=True)
class CheckOutcome:
    no: int
    name: str
    sql: str
    violations: tuple[str, ...] = ()
    #: 上线门禁：不为 0 不许交付（03 §7 末段）
    gate: bool = False

    @property
    def ok(self) -> bool:
        return not self.violations

    def line(self) -> str:
        mark = "OK  " if self.ok else ("GATE" if self.gate else "WARN")
        tail = "" if self.ok else f" -> {len(self.violations)} 行：{list(self.violations[:5])}"
        return f"{mark} {self.no:02d} {self.name}{tail}"


@dataclass(frozen=True)
class SelfCheck:
    no: int
    name: str
    sql: str
    gate: bool = False
    #: 取哪一列当"违规行 id"（默认第一列）
    id_column: int = 0


#: 03 §7 的 17 条，逐条照抄（SQL 尽量保持原样，便于与文档对照）
CHECKS: tuple[SelfCheck, ...] = (
    SelfCheck(1, "重复幂等键", "SELECT idem_key FROM tasks GROUP BY idem_key HAVING COUNT(*)>1"),
    SelfCheck(2, "卡死任务",
              "SELECT id FROM tasks WHERE state IN ('LEASED','RUNNING')"
              " AND lease_until < CAST(strftime('%s','now') AS INTEGER)-300"),
    SelfCheck(3, "无证据的已验证发现",
              "SELECT id FROM findings WHERE state='VERIFIED' AND capsule_id IS NULL", gate=True),
    SelfCheck(4, "孤儿 capsule",
              "SELECT c.id FROM capsules c LEFT JOIN findings f ON f.id=c.finding_id"
              " WHERE f.id IS NULL"),
    SelfCheck(5, "越界事件",
              "SELECT CAST(seq AS TEXT) FROM task_events WHERE kind='scope' AND ok=0"
              " AND detail_json LIKE '%VIOLATION%'", gate=True),
    SelfCheck(6, "VERIFIED 缺 route 证据",
              "SELECT id FROM capsules WHERE routeset_sha256 IS NULL OR route_ref IS NULL"),
    SelfCheck(7, "引用不存在的绑定集",
              "SELECT c.id FROM capsules c LEFT JOIN routesets r ON r.sha256=c.routeset_sha256"
              " WHERE c.routeset_sha256 IS NOT NULL AND r.sha256 IS NULL"),
    SelfCheck(8, "弱绑定给了高严重度",
              "SELECT id FROM findings WHERE route_strength IN ('crawl','probe')"
              " AND severity IN ('high','critical')", gate=True),
    SelfCheck(9, "重发现超配额",
              "SELECT id FROM tasks WHERE budget_json LIKE '%rediscovery%'"
              " AND CAST(json_extract(budget_json,'$.rediscovery.used') AS INTEGER)"
              "   > CAST(json_extract(budget_json,'$.rediscovery.max') AS INTEGER)"),
    SelfCheck(10, "绑定过期但任务仍在跑",
              "SELECT t.id FROM tasks t JOIN routesets r ON r.sha256=t.routeset_sha256"
              " WHERE t.state='RUNNING'"
              " AND r.expires_at < CAST(strftime('%s','now') AS INTEGER)-300"),
    SelfCheck(11, "来源越界（用了不在 RouteSet 里的 ref）",
              "SELECT CAST(seq AS TEXT) FROM task_events WHERE kind='binding' AND ok=0"
              " AND detail_json LIKE '%UNBOUND%'", gate=True),
    SelfCheck(12, "事实无出处",
              "SELECT id FROM blackboard_facts WHERE evidence_id IS NULL OR evidence_id=''",
              gate=True),
    SelfCheck(13, "目标数据未标记",
              "SELECT id FROM blackboard_facts WHERE origin='target' AND from_target=0",
              gate=True),
    SelfCheck(14, "资产事实指向不存在的 capsule",
              "SELECT a.id FROM asset_facts a LEFT JOIN capsules c ON c.id=a.capsule_id"
              " WHERE c.id IS NULL"),
    SelfCheck(15, "资产事实无证据或未标记",
              "SELECT id FROM asset_facts WHERE evidence_id IS NULL OR evidence_id=''"
              " OR (origin='target' AND from_target=0)", gate=True),
    SelfCheck(16, "把跑挂了当成没发现",
              "SELECT id FROM findings WHERE outcome='reported'"
              " AND state IN ('UNVERIFIABLE','BLOCKED')", gate=True),
    SelfCheck(17, "自述冒充已覆盖",
              "SELECT f.id FROM findings f LEFT JOIN task_events e"
              "   ON e.task_id=f.task_id AND e.oracle_id=f.oracle_id"
              " WHERE f.coverage_src='machine_observed' AND e.seq IS NULL", gate=True),
)


def run_all(store: Store) -> list[CheckOutcome]:
    out: list[CheckOutcome] = []
    for check in CHECKS:
        try:
            rows = store.conn.execute(check.sql).fetchall()
            values = tuple(str(r[check.id_column]) for r in rows)
        except Exception as exc:  # 表不存在等情况：**报出来**，不静默通过
            values = (f"SQL_ERROR:{type(exc).__name__}:{exc}",)
        out.append(
            CheckOutcome(no=check.no, name=check.name, sql=check.sql,
                         violations=values, gate=check.gate)
        )
    return out


def gate_failures(outcomes: list[CheckOutcome]) -> list[CheckOutcome]:
    """上线门禁（第 3/5/8/11/12/13/15/16/17 条）不为 0 即不许交付。"""
    return [o for o in outcomes if o.gate and not o.ok]


def failures(outcomes: list[CheckOutcome]) -> list[CheckOutcome]:
    return [o for o in outcomes if not o.ok]


def as_dict(outcomes: list[CheckOutcome]) -> dict[str, object]:
    return {
        "total": len(outcomes),
        "failed": len(failures(outcomes)),
        "gate_failed": len(gate_failures(outcomes)),
        "checks": [
            {"no": o.no, "name": o.name, "ok": o.ok, "gate": o.gate,
             "violations": list(o.violations[:10])}
            for o in outcomes
        ],
    }


__all__ = [
    "CHECKS",
    "CheckOutcome",
    "SelfCheck",
    "as_dict",
    "failures",
    "gate_failures",
    "run_all",
]
