// 覆盖页渲染（B401）：矩阵 + 账本缺口 + **能力边界**。
//
// 为什么单独一个文件：能力边界表让 renderCoverage 又长了一截，而 index.html 有
// ≤700 行的预算（H06 §6）。与 router.js 同样走相对导入，零构建链、零依赖。
//
// 纪律：能力边界必须渲染在矩阵**之前**——先知道"哪些判定这一轮测不了"，
// 再看矩阵才不会把"全绿"误读成"都测过了"。

/** 类别 → 人话标签（后端给的是 kind 常量，不该出现在客户界面上）。 */
export const LIMIT_LABEL = {
  needs_sequence: "需要多步序列",
  unmeasurable: "从客户端侧测不出",
  discoverable: "契约写不进，需 --discover",
  unreachable: "两条路径都选不中",
  contract_dropped: "标签被契约解析器丢弃",
};

/** 去 markdown 强调号：账本与报告里的话是给 markdown 写的，控制台是纯文本。 */
const plain = (text) => String(text || "").replace(/\*\*/g, "");

/** 能力边界表；没有边界时返回 null（**不留空标题**）。 */
export function renderLimits(el, list) {
  if (!(list || []).length) return null;
  const wrap = el("div");
  wrap.append(el("p", { class: "muted" },
    "能力边界（这些判定这一轮不可能给出结论；既不算未覆盖，也不算已覆盖）："));
  const table = el("table");
  table.append(el("tr", ...["类别", "判定", "原因"].map((h) => el("th", {}, h))));
  for (const item of list) {
    table.append(el("tr",
      el("td", {}, LIMIT_LABEL[item.kind] || String(item.kind)),
      el("td", {}, String(item.check)),
      el("td", {}, plain(item.why))));
  }
  wrap.append(table);
  return wrap;
}

/**
 * 覆盖视图：能力边界 → 口径 → 账本（缺口）→ 矩阵 → 零命中标签。
 * ``labelMap`` 是状态码的中文标签（页面里的 COVERAGE_LABEL）：由调用方传，
 * 因为同一份文案也被别处使用，搬进来会变成两处维护。
 */
export function renderCoverage(el, d, labelMap) {
  const wrap = el("div");
  const limits = renderLimits(el, d.capability_limits);
  if (limits) wrap.append(limits);
  wrap.append(el("p", { class: "muted" }, `口径：${d.scope || "unknown"}（分母只含跑过的面）`));
  // B97：有账本就先把"哪条面根本没判定"摆出来——它比矩阵更接近客户问的那个问题。
  if (d.ledger) {
    const L = d.ledger;
    const s = L.summary || {};
    const done = (L.completeness || {}).complete;
    wrap.append(el("p", {},
      `${L.scope === "platform" ? "平台合计账本" : "账本"}：面 ${s.faces || 0}（有判定 ${s.faces_covered || 0}）· 适用 ${s.applicable_pairs || 0} 对`
      + ` · 执行 ${s.executed || 0} · 缺口 ${s.gaps || 0} · 完整：${done ? "是" : "否"}`));
    for (const c of ((L.completeness || {}).caveats || [])) {
      wrap.append(el("p", { class: "muted" }, plain(c)));
    }
    const gaps = L.gaps || [];
    if (gaps.length) {
      const t = el("table");
      t.append(el("tr", ...["缺口", "严重度", "说明"].map((h) => el("th", {}, h))));
      for (const g of gaps) {
        t.append(el("tr", el("td", {}, String(g.kind)),
          el("td", {}, String(g.severity)), el("td", {}, plain(g.detail || ""))));
      }
      wrap.append(t);
    }
  }
  if (!(d.matrix || []).length) {
    wrap.append(el("p", { role: "status" }, "本轮没有可展示的覆盖数据"));
    return wrap;
  }
  const table = el("table");
  table.append(el("tr", el("th", {}, "资产"), ...(d.oracles || []).map((o) => el("th", {}, o))));
  for (const row of d.matrix) {
    const tr = el("tr", el("td", {}, row.asset));
    for (const o of d.oracles || []) {
      const st = row.cells[o] || "not_run";
      tr.append(el("td", { class: "cell-" + st }, labelMap[st] || st));
    }
    table.append(tr);
  }
  wrap.append(table);
  if ((d.zero_hit_tags || []).length) {
    wrap.append(el("p", { class: "muted" },
      ...["零命中标签：", ...d.zero_hit_tags.map((x) => el("span", { class: "chip" }, x))]));
  }
  return wrap;
}
