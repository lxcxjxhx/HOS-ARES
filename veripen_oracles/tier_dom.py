"""DOM XSS 判定（CWE-79）—— B117，DOM XSS 三步里的第 2 步。

与 `xss_reflected_ctx` 的区别：

| | 依据 | 传输 |
|---|---|---|
| 反射型 | **响应字节**里载荷原样出现 | HTTP |
| DOM 型 | **脚本真的执行了**（标记位被写） | 浏览器（`RenderTransport`） |

判定输入仍然是 `Trace.body`，只不这里面的 body 是渲染传输合成的观测 JSON：

    {"marker": "1", "outcome": "ok", "dom_sha256": "…", "path": "<面模板>"}

三条件（缺一不判 VERIFIED）：
1. **probe** 的标记位被写（真执行）；
2. **baseline** 不写（不是页面本来就有的）；
3. **control** 不写（不是标记位自己抖动，而是载荷真的被当成代码）。
"""

from __future__ import annotations

import json
from typing import Any, Mapping

from pydantic import BaseModel, ConfigDict

from veripen_core.models import ImpactClass, Judgement

from .common.base import BaseOracle, OracleMeta, transform, variant
from .common.harness import Pair, Trace, Variant

#: 载荷写死的标记位名。**判据是"它被写"，不是"页面看起来像注入"**。
MARKER = "__vp_xss"
#: 标记位写入语句：只有真执行才会跑。
FIRE = "window." + MARKER + "=1"
#: 探针载荷：图片加载失败触发 onerror。
PAYLOAD = '<img src=x onerror="' + FIRE + '">'


class DomParams(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _marker_of(trace: Trace) -> str:
    """从渲染传输合成的 body 里取标记位；取不到就当空（**不猜**）。"""
    try:
        data = json.loads(trace.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return ""
    if not isinstance(data, Mapping):
        return ""
    return str(data.get("marker") or "")


class XssDom(BaseOracle):
    """DOM XSS：载荷进 DOM 后**真的执行**才算成立。"""

    params_model = DomParams

    meta = OracleMeta(
        id="xss_dom",
        version="1.0.0",
        cwe="CWE-79",
        impact=ImpactClass.READ_ONLY,
        severity_rule="dom_sink_execution",
        selector_tags=("param_injectable",),
        # 有对象读取的面通常把参数回显到页面里，优先试
        prefer_tags=("object_read",),
    )

    #: 这个 oracle 需要**浏览器**而不是 HTTP 传输（接线在 CLI 侧）
    needs_render = True

    def baseline_params(self, binding: Any) -> dict[str, str]:
        first = (binding.param_names or ("q",))[0]
        return {first: "hello"}

    def plan(self, binding: Any) -> list[Variant]:
        return [
            variant("dom_baseline", "baseline", expect="normal",
                    tf=transform("replace", "hello", note="无载荷基线")),
            variant("dom_probe", "probe", expect="normal",
                    tf=transform("replace", PAYLOAD, note="图片 onerror 写标记位")),
            variant("dom_control", "control", expect="normal",
                    tf=transform("replace", "plain-{{nonce}}", note="普通文本：不该执行")),
        ]

    def judge(self, pairs: list[Pair]) -> Judgement:
        by_role: dict[str, Pair] = {}
        for p in pairs:
            by_role.setdefault(p.variant.role, p)
        probe = by_role.get("probe")
        control = by_role.get("control")
        baseline = by_role.get("baseline")
        if probe is None or control is None or baseline is None:
            return self.inconclusive("missing_variant")

        probe_mark = _marker_of(probe.trace)
        control_mark = _marker_of(control.trace)
        base_mark = _marker_of(baseline.trace)

        assertion = {
            "kind": "dom_xss_execution",
            "statement": "载荷进入 DOM 后被执行（标记位被写），且基线/对照都不写",
            "expected": {"probe_marker": "1", "baseline_marker": "", "control_marker": ""},
            "observed": {
                "probe_marker": probe_mark,
                "baseline_marker": base_mark,
                "control_marker": control_mark,
                "probe_status": probe.trace.status,
            },
            "fingerprint": {"executed": probe_mark == "1"},
        }
        if probe_mark == "1" and base_mark != "1" and control_mark != "1":
            return self.verified("dom_marker_fired", assertion, 0.85)
        if probe.trace.error:
            return self.inconclusive("render_failed", assertion)
        return self.refuted("marker_not_fired", assertion)


__all__ = ["FIRE", "MARKER", "PAYLOAD", "XssDom"]
