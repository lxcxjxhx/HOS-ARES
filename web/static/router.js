// 前端纯函数层（B39）：URL 状态 / 筛选 / 排序。
//
// 为什么单独一个文件：这些是**纯函数**，可以在 node 里直接跑（tests/unit/test_ui_router.py），
// 不需要浏览器。页面外壳仍是一份静态 HTML，零构建链、零依赖（H06 §6）。
//
// 约定：hash 是唯一状态源。视图 == f(hash, payload)。

export const OPS = [
  "overview", "engagement", "findings", "finding", "unverified",
  "coverage", "routes", "audit", "bounds", "approvals", "report",
];

/** 允许出现在 hash 里的参数（未知键丢弃，避免 URL 长毛） */
export const PARAMS = ["q", "sev", "oracle", "asset", "queue", "strength", "sort", "low", "id"];

export const DEFAULT_SORT = "-priority";

const SEV_RANK = { critical: 4, high: 3, medium: 2, low: 1, info: 0 };

export function normalizeOp(op) {
  return OPS.includes(op) ? op : "overview";
}

/** 页内锚点（`#main`）不是路由：跳过它，别把"跳到主内容"当成换页 */
export function isRouteHash(hash) {
  const value = String(hash || "");
  return value === "" || value.startsWith("#/");
}

/** `#/findings?q=xss&sev=high` → { op, params } */
export function parseHash(hash) {
  const raw = String(hash || "").replace(/^#\/?/, "");
  const [opPart, queryPart = ""] = raw.split("?");
  const params = {};
  for (const pair of queryPart.split("&")) {
    if (!pair) continue;
    const idx = pair.indexOf("=");
    if (idx < 0) continue;
    const key = decodeURIComponent(pair.slice(0, idx));
    const value = decodeURIComponent(pair.slice(idx + 1));
    if (!PARAMS.includes(key) || value === "") continue;
    params[key] = value;
  }
  return { op: normalizeOp(opPart), params };
}

/** { op, params } → 规范化 hash（键排序、空值丢弃），保证 round-trip 逐字相同 */
export function buildHash(state) {
  const op = normalizeOp(state.op);
  const params = state.params || {};
  const parts = [];
  for (const key of [...PARAMS].sort()) {
    const value = params[key];
    if (value === undefined || value === null || value === "") continue;
    parts.push(`${encodeURIComponent(key)}=${encodeURIComponent(String(value))}`);
  }
  return `#/${op}` + (parts.length ? `?${parts.join("&")}` : "");
}

/** 列表筛选：q 匹配 title/oracle/asset；sev 支持逗号多选；其余精确匹配 */
export function filterRows(rows, params = {}) {
  const q = (params.q || "").toLowerCase();
  const sevs = params.sev ? params.sev.split(",") : null;
  return rows.filter((row) => {
    if (q) {
      const hay = `${row.title || ""} ${row.oracle || ""} ${row.asset || ""}`.toLowerCase();
      if (!hay.includes(q)) return false;
    }
    if (sevs && !sevs.includes(row.severity)) return false;
    for (const key of ["oracle", "asset", "queue", "strength"]) {
      if (params[key] && String(row[key] ?? "") !== params[key]) return false;
    }
    return true;
  });
}

/** 排序：默认 -priority；severity 按档位；同分用 (oracle, id) 稳定兜底 */
export function sortRows(rows, sort = DEFAULT_SORT) {
  const desc = sort.startsWith("-");
  const key = desc ? sort.slice(1) : sort;
  const value = (row) => {
    if (key === "severity") return SEV_RANK[row.severity] ?? -1;
    if (key === "priority") return row.priority ?? 0;
    return String(row[key] ?? "");
  };
  return [...rows].sort((a, b) => {
    const va = value(a);
    const vb = value(b);
    let cmp = 0;
    if (typeof va === "number" && typeof vb === "number") cmp = va - vb;
    else cmp = String(va).localeCompare(String(vb));
    if (cmp !== 0) return desc ? -cmp : cmp;
    return String(a.oracle || "").localeCompare(String(b.oracle || ""))
      || String(a.id || "").localeCompare(String(b.id || ""));
  });
}

/** 一次算完：筛选 + 排序（供渲染器直接用，也便于 node 里断言） */
export function viewRows(rows, params = {}) {
  return sortRows(filterRows(rows, params), params.sort || DEFAULT_SORT);
}
