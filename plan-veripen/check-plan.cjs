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

if (bad.length) {
  console.log('\nISSUES:');
  for (const b of bad) console.log(' -', b);
  process.exit(1);
}
console.log('structure OK');
