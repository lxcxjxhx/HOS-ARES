// plan-veripen 自检：结构 + 交叉引用 + schema 可解析 + 零地址
// 用法：node check-plan.cjs
//
// v3 新增：把 check-noroute 作为必过项串进来。
// 理由：如果"零地址"只是另一条命令，它会被忘掉；串进结构自检才会成为"复制完整性"的一部分。
const fs = require('fs');
const path = require('path');
const { execFileSync } = require('child_process');

const mdFiles = fs.readdirSync('.').filter(x => x.endsWith('.md')).sort();
let total = 0;
const bad = [];

for (const f of mdFiles) {
  const t = fs.readFileSync(f, 'utf8');
  const fences = (t.match(/^```/gm) || []).length;
  if (fences % 2) bad.push(f + ':odd-fence');
  if (!/^# /.test(t)) bad.push(f + ':no-h1');
  total += t.split('\n').length;
}

console.log('docs:', mdFiles.length, 'lines:', total);

// 交叉引用：`NN-xxx` 必须能对上真实文件名（去掉 .md 比较）
const names = new Set(mdFiles.map(f => f.replace(/\.md$/, '')));
for (const f of mdFiles) {
  const t = fs.readFileSync(f, 'utf8');
  const re = /`(\d{2}-[^`\s]{2,40})`/g;
  let m;
  while ((m = re.exec(t))) {
    const ref = m[1].replace(/\.md$/, '');
    if (!names.has(ref)) bad.push(`${f} -> dangling ref: ${m[1]}`);
  }
}

// schemas/*.json 必须能解析
if (fs.existsSync('schemas')) {
  for (const f of fs.readdirSync('schemas').filter(x => x.endsWith('.json'))) {
    try {
      JSON.parse(fs.readFileSync(path.join('schemas', f), 'utf8'));
    } catch (e) {
      bad.push(`schemas/${f}: invalid json (${e.message})`);
    }
  }
  console.log('schemas:', fs.readdirSync('schemas').filter(x => x.endsWith('.json')).length);
}

// ── v3：零地址闸门（27 §7 / A9.1）──────────────────────────
let noroute = 'SKIPPED';
try {
  const out = execFileSync(process.execPath, ['check-noroute.cjs', '--root', '.'], {
    encoding: 'utf8',
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  noroute = out.trim().split('\n').pop();
  if (!/OK: 0 命中/.test(out)) bad.push('noroute: 未达到 0 命中');
} catch (e) {
  const out = (e.stdout || '') + (e.stderr || '');
  // allowlist 超限/格式错（exit 2）与命中（exit 1）都要报
  noroute = out.trim().split('\n').pop() || `exit ${e.status}`;
  bad.push('noroute failed: ' + noroute);
}
console.log('noroute:', noroute);

// ── v3：标签字典三处一致（18 §6 ↔ routeset.schema ↔ profile.schema）──
try {
  const md18 = fs.readFileSync('18-Oracle目录（14类判定矩阵）.md', 'utf8');
  const sec = md18.slice(md18.indexOf('## 6. 标签字典'));
  const fromDoc = new Set(
    (sec.match(/^\| `([a-z_]+)` \|/gm) || []).map(l => l.match(/`([a-z_]+)`/)[1])
  );
  const rs = JSON.parse(fs.readFileSync('schemas/routeset.schema.json', 'utf8'));
  const fromRoute = new Set(
    rs.properties.bindings.items.properties.semantic_tags.items.enum
  );
  const pf = JSON.parse(fs.readFileSync('schemas/profile.schema.json', 'utf8'));
  const fromProfile = new Set(
    pf.properties.routes.properties.selectors.items.properties.semantic_tag.enum
  );
  const diff = (a, b) => [...a].filter(x => !b.has(x)).sort();
  const d1 = diff(fromDoc, fromRoute), d2 = diff(fromRoute, fromDoc);
  const d3 = diff(fromDoc, fromProfile), d4 = diff(fromProfile, fromDoc);
  if (d1.length) bad.push(`tags: 18 有但 routeset 缺: ${d1.join(',')}`);
  if (d2.length) bad.push(`tags: routeset 有但 18 缺: ${d2.join(',')}`);
  if (d3.length) bad.push(`tags: 18 有但 profile 缺: ${d3.join(',')}`);
  if (d4.length) bad.push(`tags: profile 有但 18 缺: ${d4.join(',')}`);
  // 逐行核对 18 §0 总表的 selector_tags 列（反引号内所有词都必须是字典里的标签）
  const sec0 = md18.slice(md18.indexOf('## 0. 总表'), md18.indexOf('## 1. A 档'));
  const tagRows = sec0.split(/\r?\n/).filter(l => /^\| [ABC] \| `/.test(l));
  for (const row of tagRows) {
    const cells = row.split('|').map(c => c.trim());
    const oracleId = (cells[2] || '').replace(/`/g, '');
    const tagsCell = cells[4] || '';
    const used = [...tagsCell.matchAll(/`([^`]+)`/g)].map(m => m[1]);
    if (!used.length) bad.push(`tags: ${oracleId} 的 selector_tags 未用反引号标出`);
    for (const t of used) {
      if (!fromDoc.has(t)) bad.push(`tags: ${oracleId} 引用了未登记标签 ${t}`);
    }
  }
  if (tagRows.length !== 14) bad.push(`tags: 18 §0 总表应 14 行 oracle，实际 ${tagRows.length}`);

  if (fromDoc.size !== 18) bad.push(`tags: 18 §6 应有 18 个，实际 ${fromDoc.size}`);
  else if (!d1.length && !d2.length && !d3.length && !d4.length)
    console.log('tags:', fromDoc.size, 'consistent');
} catch (e) {
  bad.push('tags check failed: ' + e.message);
}

// ── v3.1：playbook schema 与 31 的 action 封闭集一致 ────────────
try {
  const pb = JSON.parse(fs.readFileSync('schemas/playbook.schema.json', 'utf8'));
  const fromSchema = new Set(pb.properties.steps.items.properties.action.enum);
  const md31 = fs.readFileSync('31-Playbook规格.md', 'utf8');
  const sec3 = md31.slice(md31.indexOf('## 3. `action` 枚举'), md31.indexOf('## 4.'));
  const fromDoc = new Set(
    (sec3.match(/^\| `([a-z_]+)` \|/gm) || []).map(l => l.match(/`([a-z_]+)`/)[1])
  );
  const onlyDoc = [...fromDoc].filter(x => !fromSchema.has(x)).sort();
  const onlySchema = [...fromSchema].filter(x => !fromDoc.has(x)).sort();
  if (onlyDoc.length) bad.push(`playbook: 31 有但 schema 缺: ${onlyDoc.join(',')}`);
  if (onlySchema.length) bad.push(`playbook: schema 有但 31 缺: ${onlySchema.join(',')}`);
  if (!onlyDoc.length && !onlySchema.length) console.log('playbook actions:', fromDoc.size, 'consistent');
} catch (e) {
  bad.push('playbook check failed: ' + e.message);
}

// ── v3.1：索引全量覆盖（00 §8 + README 都必须提到每一篇编号文档）──
try {
  const docs = mdFiles
    .filter(f => /^\d{2}-.+\.md$/.test(f))
    .map(f => f.replace(/\.md$/, ''));
  const idx00 = fs.readFileSync('00-总规划.md', 'utf8');
  const idxRd = fs.readFileSync('README.md', 'utf8');
  const miss = (txt) => docs.filter(d => !txt.includes(d));
  for (const d of miss(idx00)) bad.push(`index: 00 §8 缺 ${d}`);
  for (const d of miss(idxRd)) bad.push(`index: README 缺 ${d}`);
  if (!miss(idx00).length && !miss(idxRd).length) console.log('index:', docs.length, 'docs covered');
} catch (e) {
  bad.push('index check failed: ' + e.message);
}

// ── v3.1：00 §8 索引表的每行必须 4 列（`| 卷 | 文件 | 内容 | 谁看 |`）──
// 挡的是"漏写首列 / 漏写尾竖线"这类会让行整行塌掉的静默腐化
try {
  const t00 = fs.readFileSync('00-总规划.md', 'utf8');
  const s = t00.indexOf('## 8. 文档索引');
  const e = t00.indexOf('## 9.');
  const sec = t00.slice(s, e);
  const usec = sec.split(/\r?\n/).filter(l => l.trim().startsWith('|'));
  let rowNo = 0;
  for (const row of usec) {
    // 跳过表头与分隔行
    if (/^\|\s*卷\s*\|/.test(row) || /^\|[\s\-:|]+\|$/.test(row)) { rowNo++; continue; }
    rowNo++;
    if (!row.trim().endsWith('|')) {
      bad.push(`index: 00 §8 第 ${rowNo} 行缺尾竖线: ${row.slice(0, 50)}`);
      continue;
    }
    const cols = row.trim().split('|').length - 2; // 去掉首尾空串
    if (cols !== 4) bad.push(`index: 00 §8 第 ${rowNo} 行应为 4 列，实际 ${cols}: ${row.slice(0, 50)}`);
  }
  console.log('index rows:', rowNo - 2, 'all 4 columns');
} catch (e) {
  bad.push('index-shape check failed: ' + e.message);
}

// 版本一致性：README 与 00 的版本号必须一致
const verOf = f => {
  const t = fs.readFileSync(f, 'utf8');
  const m = t.match(/plan-v(\d+\.\d+)/);
  return m ? m[1] : null;
};
const v00 = verOf('00-总规划.md');
const vReadme = verOf('README.md');
if (v00 && vReadme && v00 !== vReadme) {
  bad.push(`version skew: 00 says v${v00}, README says v${vReadme}`);
} else if (v00) {
  console.log('version:', 'v' + v00);
}

// ── v3.6：断言 ↔ 命令 ↔ 落地状态表必须自洽（39 §2.2 I14.3）──────
// 挡的是本轮实际发生的事故：断言写进规格，命令从来没写过，于是"从未被跑过"无人知晓。
// 规则：16 §4.1 的「命令是否存在」列必须是 ✅/⚠/❌ 三者之一，
//       且**凡标 ✅ 的，命令名必须真的能在仓库 Makefile 里找到**（可跳过仓库不在同级的情况）。
try {
  const t16 = fs.readFileSync('16-完整范围与交付地图.md', 'utf8');
  const start = t16.indexOf('### 4.1 断言 ↔ 命令 ↔ 落地状态');
  if (start < 0) {
    bad.push('I14: 16 §4.1 断言↔命令表缺失（v3.6 必需）');
  } else {
    const sec = t16.slice(start, t16.indexOf('\n## ', start));
    const rows = sec.split(/\r?\n/).filter(l => /^\|\s*(A\d+|\*\*A\d+)/.test(l.replace(/^\|\s*\*\*/, '| **')));
    const seen = new Set();
    for (const row of rows) {
      const cells = row.split('|').map(c => c.trim());
      const idCell = (cells[1] || '').replace(/\*/g, '').trim();
      const idMatch = idCell.match(/^(A\d+)/);
      if (!idMatch) continue;
      const id = idMatch[1];
      const cmdCell = cells[2] || '';
      const existsCell = cells[3] || '';
      if (seen.has(id)) bad.push(`I14: 16 §4.1 断言 ${id} 重复出现`);
      seen.add(id);
      if (!/[✅⚠❌]/.test(existsCell)) {
        bad.push(`I14: 断言 ${id} 的「命令是否存在」列缺状态标记（应含 ✅/⚠/❌）`);
      }
      // 命令名提取：`make xxx` 或 make xxx
      const mk = cmdCell.match(/make\s+([A-Za-z0-9_.-]+)/);
      if (mk && existsCell.includes('✅')) {
        const target = mk[1];
        const mkPath = path.join('..', 'Makefile');
        if (fs.existsSync(mkPath)) {
          const mkTxt = fs.readFileSync(mkPath, 'utf8');
          const has = new RegExp('^' + target.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + ':', 'm').test(mkTxt);
          if (!has) bad.push(`I14: 断言 ${id} 标 ✅ 但 Makefile 无目标 "${target}"（断言与验收脱节）`);
        }
      }
    }
    if (seen.size !== 12) bad.push(`I14: 16 §4.1 应有 12 条断言，实际 ${seen.size}`);
    const missing = [...seen].filter(id => {
      const row = rows.find(r => (r.split('|').map(c => c.trim())[1].replace(/\*/g, '').trim().match(/^(A\d+)/) || [])[1] === id);
      return row && /❌/.test(row.split('|').map(c => c.trim())[3] || '');
    });
    console.log('I14 assertions:', seen.size, 'with commands missing:', missing.length,
      missing.length ? `(${missing.join(',')})` : '');
  }
} catch (e) {
  bad.push('I14 check failed: ' + e.message);
}

if (bad.length) {
  console.log('\nISSUES:');
  for (const b of bad) console.log(' -', b);
  process.exit(1);
}
console.log('structure OK');
