# VeriPen Playbook（现场四段，每段五步）

## 一、装不上

1. `make doctor` —— 看第一行 FAIL
2. 若 `deps` 失败 → `python -m pip install -r requirements.lock`
3. 若 `bindings` 失败 → 检查 `config/bindings/service.yaml` 是否被改
4. 若 `default-off` 失败 → 有人把写操作类放进了 `enabled`，**这是安全回归**
5. 全绿后 `make demo-offline`（不依赖网络）确认链路通

## 二、跑不动

1. `make demo-offline` —— 只走判定纯函数，验证内核没坏
2. 若离线也失败 → 是代码问题，`pytest tests -x` 定位
3. 若离线成功但在线失败 → 网络/模型；看 `budget` 是否 `budget_exhausted`
4. 检查 discovery 三档：全空会立刻收尾（不是 bug，是"没有可测面"）
5. 仍不动 → `make bench` 确认靶场健康

## 三、结果可疑

1. `make bench` —— FP 必须为 0，否则先修对照逻辑
2. 看 `bench.md` 的「未命中用例」，区分 `no_selector_match` 与 `error`
3. `make verify-report` —— manifest 不过说明报告被改过
4. 抽一条发现跑 `make replay-offline`，结论不成立就是「疑似已修复」
5. 若是 `ROUTE_UNRESOLVED`，**不许写成已修复**——那是面没了

## 四、要立刻停

1. 把资产从 profile 的 `scope.assets` 移除
2. 重跑该任务的 replay
3. 期望：`REPLAY_BLOCKED` + **零请求**
4. 若仍有请求发出 → 这是 P0，立即停 worker 并保留 `task_events`
5. 保留现场：`out/`、capsule、日志，不要清理
