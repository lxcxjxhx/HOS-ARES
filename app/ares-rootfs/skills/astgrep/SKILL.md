---
name: astgrep
version: 1.0.0
trigger: ast, 语法树, 精准定位, 规则检测
description: ast-grep——基于 AST 的代码搜索与规则分析。比正则更理解代码结构，适合安全审计、漏洞规则检测与精准代码定位，减少误匹配与无效读取。
---

# ast-grep — AST 精准检索 Skill

## 用途
需要精准定位符号/模式/漏洞规则时，用 AST 而非正则，命中更准、读取更少。

## 推荐流程
1. 检索符号定义、调用、类/函数结构时优先用 ast-grep（结构化匹配）。
2. 编写漏洞/风格规则时用 AST 模式（pattern）而非正则字符串。
3. 命中后只读取命中的行/片段，不做大范围全文搜索。

## 典型用法（rootfs 内）
```sh
# 查找所有函数定义
sg -p 'fn $NAME($$$ARGS) {}' -l rust

# 查找 SQL 拼接（安全审计）
sg -p 'format!("...{}", $X)' -l rust
```

## 说明
ast-grep 为可执行二进制工具；未在 rootfs 内置时，Agent 按 AST 语义人工做结构化检索。
