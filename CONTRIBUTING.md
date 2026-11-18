# 贡献指南

## 环境

```bash
pip install -e ".[dev]"     # Python 3.11+
veripen doctor              # 25 项自检；缺条件的记 skip，不记 fail
```

没装进 PATH 时，把 `veripen` 换成 `python -m veripen_cli`，效果相同。

## 提交之前

**本地跑的和 CI 跑的是同一份定义** —— 清单在 `scripts/verify.py`，`.github/workflows/verify.yml`
调用的也是它。所以不要在别处再写一份命令列表。

```bash
python scripts/verify.py                 # 快闸门：mypy + 各道 lint + 账本 + 计划结构（约 1 分钟）
python scripts/verify.py --with-tests    # 再加三段 pytest（全量，本机约 1 小时）
```

**用哪个解释器**：仓库根若有 `.venv`，用它跑（`.venv/Scripts/python scripts/verify.py`）。
全局 python 往往缺 `playwright` / `mypy`，得到的是"环境红"而不是代码红（实测：同一个提交
换解释器 = 6 failed / 16 skipped ↔ 全绿）。`verify.py` 与 `veripen doctor` 第一行都会打印
它在用哪个解释器；不是 `.venv` 时会明说。
分段跑：

| 命令 | 覆盖 |
|---|---|
| `make test` | pytest |
| `make lint` | `bindings-check` + 各道 lint |
| `make types-check` | mypy |
| `make doctor` | 环境自检 |
| `make verify-all` | 全量 |

## 提交规范

Conventional Commits，scope 用模块名，描述写中文 —— 与现有历史保持一致：

```
feat(agent): 接真模型的三个环境变量，哪里都查不到
test(installer): "装完点开就是界面"只有一次运行证明，没有用例守着
feat(delivery): 交付材料的第一步就跑不通（Windows 上没有 make）
```

**描述写的是「发现的问题」，不是「改了什么」。** 这个仓的 log 当事故记录用：
读的人应该一眼看出原来哪里有个坑，而不是读一遍 diff 才知道。

## PR 检查项

- [ ] `python scripts/verify.py` 全绿（或说明哪一项为何跳过）
- [ ] 新增行为带对应用例；用例放在 `tests/` 下按分段组织，能被 `--only <chunk>` 选中
- [ ] 改了 `scripts/verify.py` 的检查项时，同步更新 `.github/workflows/verify.yml` 的注释
- [ ] 不提交 `out/`、`.veripen/` 下的运行产物
- [ ] 改动了判定逻辑时，在 PR 描述里说明「哪些输入的行为变了」——这是纯函数内核，行为变化要可枚举

## 安全

这是安全工具。漏洞报告走 [SECURITY.md](SECURITY.md)，**不要开公开 issue**。
