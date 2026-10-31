const fs = require('fs');
const crypto = require('crypto');
const path = require('path');

const files = [];
function walk(dir) {
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, e.name);
    if (e.isDirectory()) walk(p);
    else files.push(p.replace(/\\/g, '/'));
  }
}
walk('.');
const skip = f => f === 'MANIFEST.md' || f.endsWith('.zip');
const list = files.filter(f => !skip(f)).sort();

let totalBytes = 0, totalLines = 0;
const rows = [];
for (const f of list) {
  const buf = fs.readFileSync(f);
  const sha = crypto.createHash('sha256').update(buf).digest('hex');
  totalBytes += buf.length;
  let lines = 0;
  if (/\.(md|json|cjs|js|py)$/.test(f)) lines = buf.toString('utf8').split('\n').length;
  totalLines += lines;
  rows.push({ f, sha, bytes: buf.length, lines });
}

const md = `# MANIFEST · plan-veripen v3.0

> 复制后校验完整性：文件数、字节数、sha256 三项都对得上，才算拷对。
> **本文件不含自身哈希**（自指不可校验），除此之外全部文件都在表里。

| 项 | 值 |
|---|---|
| 包版本 | \`plan-v3.0\` |
| 生成时间 | ${new Date().toISOString()} |
| 文件数 | ${list.length} |
| 总字节 | ${totalBytes} |
| 总行数（文本） | ${totalLines} |
| 内容 | 28 篇规格（含 27/28）+ README + 3 个自检脚本 + allowlist + 5 份 JSON Schema |

## 校验命令

\`\`\`bash
node check-plan.cjs        # 结构 + 交叉引用 + schema 可解析 + 零地址闸门 + 版本一致
node check-noroute.cjs     # 单独跑零地址闸门（最后一行应为 OK: 0 命中）
node _verify-manifest.cjs  # 逐个文件比对 sha256
\`\`\`

## 文件清单

| 文件 | 字节 | 行 | sha256（前 16） |
|---|---:|---:|---|
${rows.map(r => `| \`${r.f}\` | ${r.bytes} | ${r.lines || '-'} | \`${r.sha.slice(0, 16)}\` |`).join('\n')}

## 完整 sha256

\`\`\`
${rows.map(r => `${r.sha}  ${r.f}`).join('\n')}
\`\`\`
`;

fs.writeFileSync('MANIFEST.md', md);
console.log('files:', list.length, 'bytes:', totalBytes, 'lines:', totalLines);
