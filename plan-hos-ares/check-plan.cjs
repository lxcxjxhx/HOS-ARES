#!/usr/bin/env node
// plan-hos-ares 自检：结构 + 引用 + 能力矩阵 + 批次一致性 + 代码锚点 + 版本 + MANIFEST
//
// 用法：
//   node check-plan.cjs          校验
//   node check-plan.cjs --write  重新生成 MANIFEST.md 后校验
//
// 为什么锚点必须机器校验：本包 70% 的论证是"看这一行代码"。
// 锚点一漂，方案就退化成意见——纪律挡不住，只有门挡得住。
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');

const HERE = __dirname;
const REPO = path.resolve(HERE, '..');
const bad = [];
const note = [];

const mdFiles = fs.readdirSync(HERE).filter((f) => f.endsWith('.md') && f !== 'MANIFEST.md').sort();
const read = (f) => fs.readFileSync(path.join(HERE, f), 'utf8');

// ── 1 结构 ──────────────────────────────────────────────────────
let totalLines = 0;
for (const f of mdFiles) {
  const t = read(f);
  if (!/^# /.test(t)) bad.push(`${f}: 缺一级标题`);
  const fences = (t.match(/^```/gm) || []).length;
  if (fences % 2) bad.push(`${f}: 代码围栏不成对（${fences}）`);
  totalLines += t.split('\n').length;
}
console.log(`docs: ${mdFiles.length}  lines: ${totalLines}`);

// ── 2 交叉引用 ──────────────────────────────────────────────────
const names = new Set(mdFiles.map((f) => f.replace(/\.md$/, '')));
for (const f of mdFiles) {
  for (const m of read(f).matchAll(/`(H\d{2}-[^`\s]{2,40})`/g)) {
    const ref = m[1].replace(/\.md$/, '');
    if (!names.has(ref)) bad.push(`${f}: 悬空引用 ${m[1]}`);
  }
}

// ── 3 H00 覆盖全部 H0X ──────────────────────────────────────────
{
  const h00 = read('H00-总纲与四波排期.md');
  const miss = [...names].filter((n) => n !== 'README' && !h00.includes(n));
  if (miss.length) bad.push(`H00 缺文档登记: ${miss.join(', ')}`);
  else note.push(`H00 覆盖 ${names.size - 1} 篇`);
}

// ── 4 能力矩阵：恰好 22 项，每项有批次或"保持" ───────────────────
{
  const h01 = read('H01-能力矩阵与对标口径.md');
  const rows = h01.split(/\r?\n/).filter((l) => /^\|\s*\*\*[VEPWG]\d\*\*\s*\|/.test(l));
  if (rows.length !== 22) bad.push(`H01 能力项应为 22，实际 ${rows.length}`);
  for (const row of rows) {
    const cells = row.split('|').map((c) => c.trim());
    const id = (cells[1] || '').replace(/\*/g, '');
    const last = cells[cells.length - 2] || '';
    if (!/B\d+/.test(last) && !/保持/.test(last)) bad.push(`H01 ${id} 的批次列既无批次号也无「保持」`);
  }
  const ids = rows.map((r) => (r.split('|')[1] || '').replace(/\*/g, '').trim());
  const dup = ids.filter((x, i) => ids.indexOf(x) !== i);
  if (dup.length) bad.push(`H01 能力项重复: ${[...new Set(dup)].join(',')}`);
  else note.push(`能力矩阵 ${rows.length} 项`);
}

// ── 5 批次：卡 ↔ H00 波表 ↔ H07 总表 ────────────────────────────
const expand = (a, b) => {
  const out = [];
  for (let i = Number(a.slice(1)); i <= Number(b.slice(1)); i++) out.push(`B${i}`);
  return out;
};
const waveRows = (text) => {
  const rows = [];
  for (const line of text.split(/\r?\n/)) {
    if (!line.startsWith('|')) continue;
    const cells = line
      .split('|')
      .map((c) => c.replace(/\*/g, '').trim())
      .filter((c) => c !== '');
    const head = cells[0] || '';
    if (!/^(W\d|合计)/.test(head)) continue;
    const rangeIdx = cells.findIndex((c) => /^B\d+[–-]B\d+$/.test(c));
    if (rangeIdx < 0) continue;
    const nums = cells.filter((c) => /^\d+(\.\d+)?$/.test(c)).map(Number);
    const [a, b] = cells[rangeIdx].split(/[–-]/);
    rows.push({ head, ids: expand(a, b), count: nums[0], days: nums[1] });
  }
  return rows;
};
{
  const h07 = read('H07-批次总表与排期.md');
  const h00 = read('H00-总纲与四波排期.md');

  const cardDays = new Map();
  for (const line of h07.split(/\r?\n/)) {
    const m = line.match(/^##\s+(B\d+)\s*·/);
    if (!m) continue;
    const d = line.match(/〔[^〕]*?([0-9]+(?:\.[0-9]+)?)\s*人天/);
    if (!d) bad.push(`H07 ${m[1]} 卡缺人天标注`);
    if (cardDays.has(m[1])) bad.push(`H07 ${m[1]} 卡重复`);
    cardDays.set(m[1], Number(d ? d[1] : NaN));
  }

  const cards = h07.split(/^## /m).slice(1).filter((c) => /^B\d+/.test(c));
  for (const card of cards) {
    const id = card.match(/^(B\d+)/)[1];
    if (!/判据/.test(card)) bad.push(`H07 ${id} 缺判据`);
    if (!/\|\s*\*{0,2}PR\*{0,2}\s*\|/.test(card)) bad.push(`H07 ${id} 缺 PR 行`);
    if (!/\|\s*\*{0,2}回滚\*{0,2}\s*\|/.test(card)) bad.push(`H07 ${id} 缺回滚行`);
  }

  const w00 = waveRows(h00).filter((r) => /^W/.test(r.head));
  const t00 = waveRows(h00).find((r) => r.head === '合计');
  const w07 = waveRows(h07).filter((r) => /^W/.test(r.head));
  const t07 = waveRows(h07).find((r) => r.head === '合计');

  if (!w00.length) bad.push('H00 缺四波排期表（含 Bxx–Byy 范围）');
  if (w00.length !== 4) bad.push(`H00 波表应 4 行，实际 ${w00.length}`);
  const key = (rows) => rows.map((r) => `${r.ids[0]}-${r.ids.at(-1)}:${r.count}:${r.days}`).join('|');
  if (key(w00) !== key(w07)) bad.push(`H00 波表与 H07 总表不一致：\n    H00 ${key(w00)}\n    H07 ${key(w07)}`);

  const byWave = new Set();
  for (const r of w00) {
    if (r.count !== r.ids.length) bad.push(`H00 波表 ${r.ids[0]}–${r.ids.at(-1)} 数量声明 ${r.count} ≠ 范围 ${r.ids.length}`);
    const sum = r.ids.reduce((s, id) => s + (cardDays.get(id) || 0), 0);
    if (Number(sum.toFixed(1)) !== Number(r.days)) {
      bad.push(`H00 波 ${r.ids[0]}–${r.ids.at(-1)} 人天 ${r.days} ≠ 卡合计 ${sum.toFixed(1)}`);
    }
    for (const id of r.ids) {
      if (!cardDays.has(id)) bad.push(`H07 缺批次卡 ${id}`);
      byWave.add(id);
    }
  }
  for (const id of cardDays.keys()) if (!byWave.has(id)) bad.push(`H07 有卡 ${id} 但不在任何波里`);

  const totalDays = [...cardDays.values()].reduce((s, d) => s + (Number.isNaN(d) ? 0 : d), 0);
  if (!t00) bad.push('H00 §2 缺「合计 | B35–B59 | 25 | 44.5」行');
  else {
    if (t00.count !== cardDays.size) bad.push(`H00 合计批次 ${t00.count} ≠ 卡数 ${cardDays.size}`);
    if (Number(t00.days) !== Number(totalDays.toFixed(1))) {
      bad.push(`H00 合计人天 ${t00.days} ≠ 卡合计 ${totalDays.toFixed(1)}`);
    }
  }
  if (t07 && t00 && (t07.count !== t00.count || Number(t07.days) !== Number(t00.days))) {
    bad.push(`H07 总表合计 ${t07.count}/${t07.days} ≠ H00 合计 ${t00.count}/${t00.days}`);
  }
  if (cardDays.size !== 25) bad.push(`本包应有 B35–B59 共 25 批，实际 ${cardDays.size}`);
  else note.push(`批次 ${cardDays.size} 批 / ${totalDays.toFixed(1)} 人天`);
}

// ── 6 代码锚点 ──────────────────────────────────────────────────
{
  const roots = [REPO, path.join(REPO, 'plan-veripen'), path.join(REPO, 'plan-hos-ares')];
  const ALIAS = {
    'console.py': 'web/console.py',
    'index.html': 'web/static/index.html',
    'serve_console.py': 'scripts/serve_console.py',
    'executor.py': 'veripen_core/workbench/executor.py',
    'fs.py': 'veripen_core/workbench/fs.py',
    'blackboard.py': 'veripen_core/blackboard.py',
    'context.py': 'veripen_core/context.py',
    'orchestrator.py': 'veripen_core/agents/orchestrator.py',
    'registry.py': 'veripen_core/tools/registry.py',
  };
  const cache = new Map();
  const lineCount = (p) => {
    if (!cache.has(p)) cache.set(p, fs.readFileSync(p, 'utf8').split('\n').length);
    return cache.get(p);
  };
  let checked = 0;
  for (const f of mdFiles) {
    for (const m of read(f).matchAll(/`([A-Za-z0-9_.\-/]+\.(?:py|html|cj?s|json|md|yml|yaml)):(\d+)(?:,(\d+))?`/g)) {
      const [, rawRel, l1, l2] = m;
      const rel = ALIAS[rawRel] || rawRel;
      const hit = roots.map((r) => path.join(r, rel)).find((p) => fs.existsSync(p) && fs.statSync(p).isFile());
      if (!hit) {
        bad.push(`${f}: 锚点指向不存在的文件 ${rawRel}`);
        continue;
      }
      const totalN = lineCount(hit);
      for (const raw of [l1, l2].filter(Boolean)) {
        const n = Number(raw);
        if (n < 1 || n > totalN) bad.push(`${f}: 锚点越界 ${rawRel}:${n}（文件共 ${totalN} 行）`);
      }
      checked += 1;
    }
  }
  note.push(`代码锚点 ${checked} 处`);
}

// ── 7 版本 ──────────────────────────────────────────────────────
{
  const ver = (f) => {
    const m = read(f).match(/plan-hos-v(\d+\.\d+)/);
    return m ? m[1] : null;
  };
  const a = ver('README.md');
  const b = ver('H00-总纲与四波排期.md');
  if (!a || !b) bad.push('README / H00 缺 plan-hos-vX.Y');
  else if (a !== b) bad.push(`版本不一致: README v${a} vs H00 v${b}`);
  else note.push(`version v${a}`);
}

// ── 8 MANIFEST ─────────────────────────────────────────────────
const files = [];
const walk = (dir) => {
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, e.name);
    if (e.isDirectory()) walk(p);
    else files.push(path.relative(HERE, p).replace(/\\/g, '/'));
  }
};
walk(HERE);
const list = files.filter((f) => f !== 'MANIFEST.md').sort();
const rows = list.map((f) => {
  const buf = fs.readFileSync(path.join(HERE, f));
  const sha = crypto.createHash('sha256').update(buf).digest('hex');
  const lines = /\.(md|cjs|js|py|json|ya?ml)$/.test(f) ? buf.toString('utf8').split('\n').length : 0;
  return { f, sha, bytes: buf.length, lines };
});

if (process.argv.includes('--write')) {
  const totalBytes = rows.reduce((s, r) => s + r.bytes, 0);
  const md = `# MANIFEST · plan-hos-ares v1.0

> 生成物，不手改：\`node check-plan.cjs --write\`。
> **不含自身哈希**（自指不可校验），其余文件全部在表里。

| 项 | 值 |
|---|---|
| 包版本 | \`plan-hos-v1.0\` |
| 生成时间 | ${new Date().toISOString()} |
| 文件数 | ${rows.length} |
| 总字节 | ${totalBytes} |
| 总行数（文本） | ${rows.reduce((s, r) => s + r.lines, 0)} |

## 校验

\`\`\`bash
node plan-hos-ares/check-plan.cjs          # 期望最后一行：structure OK
node plan-hos-ares/check-plan.cjs --write  # 内容改过之后重生成本文件
\`\`\`

## 文件清单

| 文件 | 字节 | 行 | sha256（前 16） |
|---|---:|---:|---|
${rows.map((r) => `| \`${r.f}\` | ${r.bytes} | ${r.lines || '-'} | \`${r.sha.slice(0, 16)}\` |`).join('\n')}

## 完整 sha256

\`\`\`
${rows.map((r) => `${r.sha}  ${r.f}`).join('\n')}
\`\`\`
`;
  fs.writeFileSync(path.join(HERE, 'MANIFEST.md'), md);
  console.log('manifest regenerated; files:', rows.length);
} else {
  const mf = path.join(HERE, 'MANIFEST.md');
  if (!fs.existsSync(mf)) bad.push('MANIFEST.md 缺失（先跑 --write）');
  else {
    const txt = fs.readFileSync(mf, 'utf8');
    const stale = rows.filter((r) => !txt.includes(r.sha));
    if (stale.length) bad.push(`MANIFEST 过期：${stale.map((d) => d.f).join(', ')}（跑 --write）`);
    else note.push(`MANIFEST ${rows.length} files`);
    const cnt = txt.match(/\| 文件数 \| (\d+) \|/);
    if (cnt && Number(cnt[1]) !== rows.length) bad.push(`MANIFEST 文件数 ${cnt[1]} ≠ 实际 ${rows.length}`);
  }
}

if (bad.length) {
  console.log('\nISSUES:');
  for (const b of bad) console.log(' -', b);
  process.exit(1);
}
console.log('checks:', note.join(' | '));
console.log('structure OK');
