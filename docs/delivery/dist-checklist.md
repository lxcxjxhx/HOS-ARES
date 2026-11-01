# 分发前十项

- [ ] `make doctor` 16 项全绿
- [ ] `make lint`（零地址 / 禁数据桩 / 绑定覆盖 / 默认关闭 / policy）
- [ ] `make bench` FP=0、越界=0、routeset 三轮一致
- [ ] `make demo` 与 `make demo-alt` 均跑通
- [ ] 两档 `profile_hash` 不同，且 `git diff --stat veripen_core/` 为空（除 profile 模块）
- [ ] 报告三视图零地址（`make verify-report` 通过）
- [ ] capsule 导出（`--external`）无地址、无凭据
- [ ] 离线包 manifest 的 digest 与目标机一致
- [ ] 演示素材齐备：quickref / playbook / script / 录像
- [ ] 客户侧授权范围、窗口、拒绝项已在 profile 里写明
