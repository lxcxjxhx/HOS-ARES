#!/usr/bin/env python3
"""B9 覆盖矩阵：把"测了什么 / 没测什么"打成一张可审的表。

零命中的 oracle 必须出现在输出里——那是"诚实的未覆盖"，不是隐藏项。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from veripen_oracles import ALL_ORACLES  # noqa: E402
from veripen_oracles.auth import checks  # noqa: E402


def main() -> int:
    hits = {cls.meta.id: 0 for cls in ALL_ORACLES}
    # 本批实际有靶场用例的（B5/B6/B7 + B9 的 suite）
    for oid in ("sqli_bool_diff", "idor_cross_account", "ssrf_oob", "open_redirect",
                "path_traversal", "sqli_error_based", "bola_functional", "xss_reflected_ctx",
                "mass_assignment", "jwt_suite"):
        hits[oid] = 1

    matrix = checks.coverage_matrix(oracle_hits=hits)
    print(f"checks={len(matrix['checks'])} by_automation={matrix['by_automation']}")
    print(f"manual={matrix['manual_count']}（SAML 四条：本期只给清单，不假装覆盖）")
    print(f"zero_hit={matrix['zero_hit_oracles']}")
    for row in matrix["checks"]:
        flag = "  " if row["automation"] != "manual" else "M "
        print(f"{flag}{row['id']:3} {row['automation']:6} {row['title']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
