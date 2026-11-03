#!/usr/bin/env node
// 方案包统一治理自检：注册表 ↔ 包实际版本 ↔ 时间节点 ↔ MANIFEST 哈希
//
// 用法：node scripts/plan/check-plans.cjs
// 挡的是真实发生过的事：包改了、注册表没改，于是"权威范围"这句话失效而无人发现。
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');

const REPO = path.resolve(__dirname, '..', '..');
const bad = [];
const note = [];

const read = (rel) => fs.readFileSync(path.join(REPO, rel), 'utf8');
const gov = read('PLANS.md');

// ── 1 解析注册表 ────────────────────────────────────────────────
const rows = [];
for (const line of gov.split(/\r?\n/)) {
  if (!/^\|\s*`[a-z]+`\s*\|/.test(line)) continue;
  const c = line.split('|').map((x) => x.trim().replace(/`/g, '').replace(/\*\*/g, ''));
  rows.push({ id: c[1], dir: c[2], version: c[3], date: c[4], status: c[5], cmd: c[7], sha16: c[8] });
}
if (rows.length < 2) bad.push('注册表至少应有两行（kernel + program）');
const dup = rows.map((r) => r.id).filter((x, i, a) => a.indexOf(x) !== i);
if (dup.length) bad.push(`注册表 id 重复: ${[...new Set(dup)].join(',')}`);
note.push(`注册表 ${rows.length} 包`);

// ── 2 逐包校验 ─────────────────────────────────────────────────
for (const r of rows) {
  if (r.id === 'governance') continue;

  // 目录内必须存在的三件套
  for (const f of ['README.md', 'MANIFEST.md']) {
    if (!fs.existsSync(path.join(REPO, r.dir, f))) bad.push(`${r.id}: 缺 ${r.dir}/${f}`);
  }
  if (r.cmd && !/node\s+\S+\.cjs/.test(r.cmd)) bad.push(`${r.id}: 自检命令应为 node 脚本`);
  if (r.cmd) {
    const m = r.cmd.match(/node\s+([^\s)]+\.cjs)/);
    if (!m) bad.push(`${r.id}: 自检命令里找不到 .cjs`);
    else if (![path.join(REPO, m[1]), path.join(REPO, r.dir, m[1])].some((x) => fs.existsSync(x))) {
      bad.push(`${r.id}: 自检脚本不存在 ${m[1]}`);
    }
  }

  // 版本一致：注册表 ↔ README ↔ 00/H00
  const readme = read(path.join(r.dir, 'README.md'));
  if (!readme.includes(r.version)) bad.push(`${r.id}: README 未出现版本 ${r.version}`);
  const head = fs.readdirSync(path.join(REPO, r.dir)).find((f) => /^(00|H00)-/.test(f));
  if (!head) bad.push(`${r.id}: 缺 00/H00 主文件`);
  else if (!read(path.join(r.dir, head)).includes(r.version)) bad.push(`${r.id}: ${head} 未出现版本 ${r.version}`);
  // 生效日一致
  if (!readme.includes(r.date)) bad.push(`${r.id}: README 未出现生效日 ${r.date}（时间节点制度 §3）`);

  // MANIFEST 哈希（冻结包的唯一"未被动过"证据）
  const sha = crypto.createHash('sha256').update(fs.readFileSync(path.join(REPO, r.dir, 'MANIFEST.md'))).digest('hex');
  if (!r.sha16) bad.push(`${r.id}: 注册表缺 MANIFEST sha256`);
  else if (!sha.startsWith(r.sha16)) bad.push(`${r.id}: MANIFEST 变了（注册表 ${r.sha16} vs 实际 ${sha.slice(0, 16)}）—— 改包必须同 PR 更新注册表`);

  // 时间节点：非冻结包必须有「修订记录」与「里程碑」
  if (r.status !== 'frozen') {
    const main = head ? read(path.join(r.dir, head)) : '';
    if (!/修订记录/.test(main)) bad.push(`${r.id}: ${head} 缺「修订记录」（时间节点制度 §3）`);
    if (!/里程碑/.test(main)) bad.push(`${r.id}: ${head} 缺「里程碑」（时间节点制度 §3）`);
  } else {
    note.push(`${r.id} frozen（只读）`);
  }
}

// ── 3 台账日期格式与单调 ───────────────────────────────────────
{
  const sec = gov.slice(gov.indexOf('## 8. 台账'));
  const dates = [...sec.matchAll(/\|\s*(\d{4}-\d{2}-\d{2})\s*\|/g)].map((m) => m[1]);
  if (dates.length < 3) bad.push('台账少于 3 行（制度 §8 要求只增不改）');
  for (const d of dates) if (!/^\d{4}-\d{2}-\d{2}$/.test(d)) bad.push(`台账日期格式错: ${d}`);
  const sorted = [...dates].sort();
  if (dates.join() !== sorted.join()) bad.push('台账日期非单调（新的应追加在末尾）');
  note.push(`台账 ${dates.length} 条`);
}

// ── 4 冲突：同一前缀被两个包占用 ────────────────────────────────
{
  const prefixes = new Map();
  for (const r of rows) {
    if (r.id === 'governance') continue;
    const files = fs
      .readdirSync(path.join(REPO, r.dir))
      .filter((f) => /^[A-Z]*\d{2}-.+\.md$/.test(f));
    const p = files.map((f) => f.replace(/^[A-Z]*\d+-/, '')).sort();
    for (const n of p) {
      const k = n.toLowerCase();
      if (prefixes.has(k) && prefixes.get(k) !== r.id) {
        bad.push(`同义文档名出现在两个包: ${n}（${prefixes.get(k)} / ${r.id}）`);
      }
      prefixes.set(k, r.id);
    }
  }
}

if (bad.length) {
  console.log('ISSUES:');
  for (const b of bad) console.log(' -', b);
  process.exit(1);
}
console.log('checks:', note.join(' | '));
console.log('governance OK');
