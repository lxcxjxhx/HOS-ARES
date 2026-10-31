# cyber-harness · 逆向笔记

> 来源：chainreactors/cyber-harness @ 89f8af5ea2f4 (AGPL-3.0)
> 源码：vendor/cyber-harness/（不入 git）
> 抽取面：F1 F3

## 状态

- [x] 0 建目录 + manifest.json
- [ ] 1 身份（inspect_* / sha256 / 打包形态）
- [ ] 2 字符串分类计数 + 过程索引（plan-veripen/28 §3.1）
- [ ] 2b grep 调用点再读实现
- [ ] 3 定点结构分析（F1–F6 选 1–2）
- [ ] 4 导出 evidence bundle
- [ ] 5 extracts/<n>-<主题>.md 七段式
- [ ] 6 close_binary + 置信度

## 结构快照（首轮）

```
(见 `ls vendor/cyber-harness`)
```

## 结论

（按 28 §5 七段式写入 extracts/，本文件只放过程）
