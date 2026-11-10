"""B217 · 「影响」「建议」两行的人话（由**真实元数据**生成，不是占位符）。

为什么要有这个模块：

* 真跑 `veripen run` 时 `impact` / `advice` 是空的 → 渲染器按"空就不写"处理，
  于是交付物里**没有影响、没有建议**——恰好是读者最关心的两行；
* demo 里这两行是手写占位（`见 capsule` / `按复现命令重跑`），客户读起来像没写完。

口径：只用**已经声明的元数据**推——oracle 的 `impact`（`read_only` / `reversible_write`）
与判定出的 `severity`。不编现场细节，不写"可能/疑似"这类措辞
（`veripen/report/lint.py` 对 `verified_strong` 禁这几个词）。
"""

from __future__ import annotations

from typing import Any

#: 影响面：按 oracle 声明的写入等级说人话。**不写"可能"**——这里是事实陈述。
_IMPACT: dict[str, str] = {
    "read_only": "只读取证：目标数据未被改动；被读出的内容本身即信息泄露，影响面按 CWE 归类。",
    "reversible_write": "写入可逆：证据里保留了改动前后的值，可按证据回滚。",
    "destructive": "破坏性写入：目标状态已改变，需按证据回滚。",
}

#: 建议：按严重度给下一步。三条都指向"修复后重跑本 oracle 应变为 REFUTED"这个可验证动作。
_ADVICE: dict[str, str] = {
    "high": "先在预发环境按复现命令确认影响面；修复后重跑本 oracle，判定应变为 REFUTED。",
    "medium": "修复后重跑本 oracle 做回归，判定应变为 REFUTED；期间按证据限制该接口的访问范围。",
    "low": "随版本回归时重跑本 oracle，判定应变为 REFUTED；仍为 VERIFIED 则升级严重度。",
}

_DEFAULT_IMPACT = "影响面未声明：该 oracle 没有给出写入等级，需人工判定。"
_DEFAULT_ADVICE = "修复后重跑本 oracle 做回归，判定应变为 REFUTED。"


def _value(raw: Any) -> str:
    """`ImpactClass` 或字符串都吃：枚举取 `.value`，其余按字符串小写。"""
    return str(getattr(raw, "value", raw) or "").strip().lower()


def impact_text(impact: Any) -> str:
    """oracle 声明的写入等级 → 一句人话。未知等级**如实说未声明**，不猜。"""
    return _IMPACT.get(_value(impact), _DEFAULT_IMPACT)


def advice_text(severity: Any) -> str:
    """严重度 → 下一步动作。未知严重度走保守默认（仍然可验证）。"""
    return _ADVICE.get(str(severity or "").strip().lower(), _DEFAULT_ADVICE)


__all__ = ["advice_text", "impact_text"]
