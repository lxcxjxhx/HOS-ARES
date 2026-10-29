// plan-veripen 自检：结构 + 交叉引用 + schema 可解析
// 用法：node check-plan.cjs
const fs = require('fs');
const path = require('path');

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

if (bad.length) {
  console.log('\nISSUES:');
  for (const b of bad) console.log(' -', b);
  process.exit(1);
}
console.log('structure OK');
