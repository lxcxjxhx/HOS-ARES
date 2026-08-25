#!/usr/bin/env node
// ============================================================
// HOS-ARES · MCP 演示服务器（零外部依赖）
// 用途：验证 DeepSeek-Reasonix MCP 客户端（发现 / 调用）的端到端链路
// 协议：MCP stdio —— JSON-RPC 2.0 over stdin/stdout 单行消息
// 工具：demo_add（两数相加）、demo_echo（回显）
// 运行：node tools/mcp-demo-server.js
// ============================================================
"use strict";

const readline = require("readline");

const rl = readline.createInterface({ input: process.stdin, output: process.stdout, terminal: false });

function send(msg) {
  process.stdout.write(JSON.stringify(msg) + "\n");
}
function reply(id, result) {
  send({ jsonrpc: "2.0", id, result });
}
function rpcError(id, code, message) {
  send({ jsonrpc: "2.0", id, error: { code, message, data: null } });
}

const TOOLS = [
  {
    name: "demo_add",
    description: "两数相加（HOS-ARES MCP 链路验证示例）",
    inputSchema: {
      type: "object",
      properties: {
        a: { type: "number", description: "加数 a" },
        b: { type: "number", description: "加数 b" },
      },
      required: ["a", "b"],
    },
  },
  {
    name: "demo_echo",
    description: "回显输入文本（HOS-ARES MCP 链路验证示例）",
    inputSchema: {
      type: "object",
      properties: { text: { type: "string", description: "要回显的文本" } },
      required: ["text"],
    },
  },
];

rl.on("line", (line) => {
  let msg;
  try { msg = JSON.parse(line); } catch { return; }
  if (!msg || msg.jsonrpc !== "2.0") return;

  // 通知类消息（无 id）：无需应答
  if (msg.id === undefined) return;

  switch (msg.method) {
    case "initialize": {
      const requested = (msg.params && msg.params.protocolVersion) || "2026-07-28";
      reply(msg.id, {
        protocolVersion: requested,
        capabilities: { tools: { listChanged: false } },
        serverInfo: { name: "hos-ares-demo", version: "0.1.0" },
      });
      // 初始化完成通知（部分客户端等待此通知后才发送 tools/list）
      send({ jsonrpc: "2.0", method: "notifications/initialized" });
      break;
    }
    case "tools/list":
      reply(msg.id, { tools: TOOLS });
      break;
    case "tools/call": {
      const { name } = msg.params || {};
      const args = (msg.params && msg.params.arguments) || {};
      if (name === "demo_add") {
        const a = Number(args.a);
        const b = Number(args.b);
        if (!isFinite(a) || !isFinite(b)) return rpcError(msg.id, -32602, "a / b 必须是数字");
        reply(msg.id, {
          content: [{ type: "text", text: `demo_add(${a}, ${b}) = ${a + b}` }],
          isError: false,
        });
      } else if (name === "demo_echo") {
        reply(msg.id, {
          content: [{ type: "text", text: String(args.text ?? "") }],
          isError: false,
        });
      } else {
        rpcError(msg.id, -32601, `未知工具: ${name}`);
      }
      break;
    }
    case "ping":
      reply(msg.id, {});
      break;
    default:
      rpcError(msg.id, -32601, `未实现方法: ${msg.method}`);
  }
});

rl.on("close", () => process.exit(0));