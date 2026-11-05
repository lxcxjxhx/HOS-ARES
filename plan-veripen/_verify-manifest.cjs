#!/usr/bin/env node
// 复制后运行：node _verify-manifest.cjs
const fs = require('fs');
const crypto = require('crypto');
// 行尾规范化：生成端按 LF 计算，校验端必须一致，否则 Windows 工作区（CRLF）
// 会把每一行都判成 MISMATCH —— 内容其实没变。见 _gen_manifest.cjs 的同名说明。
const normalize = (buf) => Buffer.from(buf.toString('utf8').replace(/\r\n/g, '\n'), 'utf8');
const md = fs.readFileSync('MANIFEST.md', 'utf8');
const block = md.split('```')[md.split('```').length - 2] || '';
const lines = block.trim().split('\n').filter(l => /^[a-f0-9]{64}  /.test(l));
let bad = 0;
for (const l of lines) {
  const [sha, f] = [l.slice(0, 64), l.slice(66)];
  if (!fs.existsSync(f)) { console.log('MISSING', f); bad++; continue; }
  const got = crypto.createHash('sha256').update(normalize(fs.readFileSync(f))).digest('hex');
  if (got !== sha) { console.log('MISMATCH', f); bad++; }
}
console.log(bad ? `FAILED: ${bad} file(s)` : `OK: ${lines.length} files verified`);
process.exit(bad ? 1 : 0);
