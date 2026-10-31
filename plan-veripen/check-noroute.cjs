#!/usr/bin/env node
// check-noroute.cjs —— 零地址闸门（27 §7 N3 的可执行形式）
//
// 用法：
//   node check-noroute.cjs [--root .] [--allowlist config/noroute.allowlist] [--json]
//
// 退出码：0 = 通过；1 = 命中；2 = 配置错误（allowlist 超限/格式错）
//
// 设计取舍（刻意保守）：
//   - 只扫文本，不做 AST。AST 能更准，但漏一处的代价远大于误报几十处。
//   - 误报用 allowlist 行级豁免，每行必须带理由，总量 ≤ 6。
//   - 例外整目录只有一个：docs/research/re/（逆向样本证据，28 Q1）。
'use strict';
const fs = require('fs');
const path = require('path');

const MAX_ALLOWLIST = 6;
const DEFAULT_ROOT = '.';
const DEFAULT_ALLOWLIST = 'config/noroute.allowlist';

// 整目录豁免：只有逆向证据目录（28 §0 Q1）
const EXEMPT_DIRS = [['docs', 'research', 're'].join(path.sep)];

// 文件级豁免：**定义规则本身的文档**。
// 它们必须引用"禁例长什么样"才能把规则说清楚（27 定义规则、28 定义隔离铁律）。
// 刻意写死两个文件名，不用通配：新增豁免必须改这个数组，等于强制 review。
const EXEMPT_FILES = new Set(['27-无硬路由架构.md', '28-逆向对标与借鉴规格.md']);

const SCAN_EXT = new Set([
  '.py', '.ts', '.tsx', '.js', '.cjs', '.mjs', '.json', '.yaml', '.yml',
  '.toml', '.ini', '.cfg', '.sql', '.md', '.txt', '.sh', '.ps1', '.env',
]);

// 永不扫描
const SKIP_DIRS = new Set([
  '.git', 'node_modules', '__pycache__', '.venv', 'venv', 'dist', 'build',
  '.pytest_cache', '.mypy_cache', 'out', 'reports', 'capsules',
]);

// ── 规则表（27 §7）──────────────────────────────────────────
const RULES = [
  {
    id: 'R-PATH',
    // 引号内以 / 开头的字面量；允许 <placeholder> 与 ROUTE_REF
    re: /["'`](\/[A-Za-z0-9_{}$][^"'`\s]{0,120})["'`]/g,
    why: '路径字面量',
    allow: s => /^\/<[^>]+>/.test(s) || s === '/dev/null' || s === '/tmp',
  },
  {
    id: 'R-URL',
    re: /\b[a-z][a-z0-9+.\-]{1,12}:\/\/[^\s"'`)\]}>]{3,}/gi,
    why: 'URL 字面量',
    allow: s =>
      // 我们自己的不透明引用 scheme：不是网络地址，正是"替代地址"的那个东西
      /^(route|vault|op):\/\//i.test(s) ||
      // 测试载荷里的本地 scheme（如 kid 注入），不是网络目标
      /^file:\/\/\/?/i.test(s) ||
      // 保留 TLD —— 按 RFC 2606 永不解析
      /^[a-z][a-z0-9+.\-]*:\/\/[^/]*\.invalid(?:[:/]|$)/i.test(s) ||
      // JSON Schema / OpenAPI 标识符，不是可请求地址
      /json-schema\.org|w3\.org|xmlns|schemas\.microsoft|sarif|openapis\.org|spdx\.org|creativecommons/i.test(s) ||
      // 文档里的占位 scheme
      /^[a-z]+:\/\/<[^>]+>$/i.test(s),
  },
  {
    id: 'R-HOST',
    re: /\b(localhost|127\.0\.0\.1|0\.0\.0\.0|[a-z0-9-]+\.(local|internal|localdomain|svc|cluster\.local))\b/gi,
    why: '主机名/回环字面量',
    allow: s => /\.invalid$/i.test(s),
  },
  {
    id: 'R-PORT',
    re: /(?:^|[^\w])(?:port|PORT)\s*[=:]\s*(\d{2,5})\b|:(\d{2,5})\/(?:tcp|udp)\b/g,
    why: '端口字面量',
    allow: () => false,
  },
  {
    id: 'R-VERB',
    re: /["'`]\s*(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+\/[^"'`]{0,120}["'`]/g,
    why: '"VERB /path" 字面量',
    allow: () => false,
    // 只对代码/配置生效：散文里引用"禁例长什么样"是必要的
    codeOnly: true,
  },
  {
    id: 'R-DEFAULT',
    // 27 §4 禁止的兜底符号
    re: /\b(fallback_path|default_route|retry_with_literal|DEFAULT_ENDPOINT|DEFAULT_PATH)\b/g,
    why: '兜底路径符号（27 §4 明令禁止）',
    allow: () => false,
    // 同上：文档必须能写出这两个词才能说明"不许用"
    codeOnly: true,
  },
];

// ── allowlist ──────────────────────────────────────────────
function loadAllowlist(file) {
  if (!fs.existsSync(file)) return [];
  const lines = fs.readFileSync(file, 'utf8').split(/\r?\n/);
  const out = [];
  for (const raw of lines) {
    const line = raw.trim();
    if (!line || line.startsWith('#')) continue;
    const tab = raw.indexOf('\t');
    if (tab < 0) {
      console.error(`allowlist: 缺理由（需 <正则> TAB <理由>）：${line}`);
      process.exit(2);
    }
    const re = raw.slice(0, tab).trim();
    const reason = raw.slice(tab + 1).trim();
    if (!reason) {
      console.error(`allowlist: 理由为空：${re}`);
      process.exit(2);
    }
    try {
      out.push({ re: new RegExp(re), reason, src: re });
    } catch (e) {
      console.error(`allowlist: 正则非法 (${re}): ${e.message}`);
      process.exit(2);
    }
  }
  if (out.length > MAX_ALLOWLIST) {
    console.error(
      `allowlist: ${out.length} 行 > 上限 ${MAX_ALLOWLIST}。` +
        `\n超过上限说明有真路由混进来了——先修代码，别加白（27 §7）。`
    );
    process.exit(2);
  }
  return out;
}

// ── 遍历 ───────────────────────────────────────────────────
function walk(dir, files) {
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, e.name);
    const rel = path.relative(root, p);
    if (e.isDirectory()) {
      if (SKIP_DIRS.has(e.name)) continue;
      if (EXEMPT_DIRS.some(d => rel === d || rel.startsWith(d + path.sep))) continue;
      walk(p, files);
      continue;
    }
    if (!SCAN_EXT.has(path.extname(e.name).toLowerCase())) continue;
    // allowlist 文件本身会被 R-URL 之类命中，跳过
    if (path.basename(p) === 'noroute.allowlist') continue;
    if (path.basename(p) === 'check-noroute.cjs') continue; // 规则定义自己
    if (EXEMPT_FILES.has(path.basename(p))) continue; // 规则定义文档（见上方注释）
    files.push(p);
  }
}

// ── 主流程 ─────────────────────────────────────────────────
const argv = process.argv.slice(2);
function arg(name, def) {
  const i = argv.indexOf(name);
  return i >= 0 && argv[i + 1] ? argv[i + 1] : def;
}
const root = path.resolve(arg('--root', DEFAULT_ROOT));
const allowFile = path.resolve(root, arg('--allowlist', DEFAULT_ALLOWLIST));
const asJson = argv.includes('--json');

if (!fs.existsSync(root)) {
  console.error(`root 不存在：${root}`);
  process.exit(2);
}

const allow = loadAllowlist(allowFile);
const files = [];
walk(root, files);
files.sort();

// codeOnly 规则只扫代码/配置，不扫散文。
// 理由：文档必须能引用"禁例长什么样"（否则规则本身讲不清），
// 而代码里出现这些符号一律是违规。这条区分让"文档可读"与"闸门有效"同时成立。
const CODE_EXT = new Set([
  '.py', '.ts', '.tsx', '.js', '.cjs', '.mjs', '.json', '.yaml', '.yml',
  '.toml', '.ini', '.cfg', '.sql', '.sh', '.ps1', '.env',
]);

let hits = 0;
const report = [];

for (const f of files) {
  const isCode = CODE_EXT.has(path.extname(f).toLowerCase());
  const text = fs.readFileSync(f, 'utf8');
  const lines = text.split(/\r?\n/);
  lines.forEach((line, i) => {
    // 行级 allowlist 优先（作用域：该行任意位置命中正则即豁免）
    if (allow.some(a => a.re.test(line))) return;
    for (const rule of RULES) {
      if (rule.codeOnly && !isCode) continue;
      rule.re.lastIndex = 0;
      let m;
      while ((m = rule.re.exec(line))) {
        const matched = m[0];
        if (rule.allow && rule.allow(matched)) continue;
        hits++;
        report.push({
          file: path.relative(root, f).replace(/\\/g, '/'),
          line: i + 1,
          rule: rule.id,
          why: rule.why,
          snippet: line.trim().slice(0, 140),
        });
        break; // 一行一条，避免同一行多个规则刷屏
      }
    }
  });
}

if (asJson) {
  console.log(JSON.stringify({ hits, allowlist: allow.length, findings: report }, null, 2));
} else {
  console.log(`noroute: 扫描 ${files.length} 文件 · allowlist ${allow.length}/${MAX_ALLOWLIST}`);
  for (const r of report) {
    console.log(`  ${r.file}:${r.line}  [${r.rule}] ${r.why}`);
    console.log(`      ${r.snippet}`);
  }
  if (hits === 0) {
    console.log('OK: 0 命中');
  } else {
    console.log(`\nFAILED: ${hits} 处（目标：0；例外只能加到 allowlist，每行带理由且总数 ≤ ${MAX_ALLOWLIST}）`);
  }
}

process.exit(hits === 0 ? 0 : 1);
