#!/usr/bin/env node
// 复制后运行：node _verify-manifest.cjs
const fs = require('fs');
const crypto = require('crypto');
const md = fs.readFileSync('MANIFEST.md', 'utf8');
const block = md.split('```')[md.split('```').length - 2] || '';
const lines = block.trim().split('\n').filter(l => /^[a-f0-9]{64}  /.test(l));
let bad = 0;
for (const l of lines) {
  const [sha, f] = [l.slice(0, 64), l.slice(66)];
  if (!fs.existsSync(f)) { console.log('MISSING', f); bad++; continue; }
  const got = crypto.createHash('sha256').update(fs.readFileSync(f)).digest('hex');
  if (got !== sha) { console.log('MISMATCH', f); bad++; }
}
console.log(bad ? `FAILED: ${bad} file(s)` : `OK: ${lines.length} files verified`);
process.exit(bad ? 1 : 0);
