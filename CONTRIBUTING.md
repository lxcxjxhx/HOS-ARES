# 贡献指南

## 环境

```bash
pip install -e ".[dev]"     # Python 3.11+
hos-ares doctor              # 25 项自检；缺条件的记 skip，不记 fail
```

没装进 PATH 时，把 `hos-ares` 换成 `python -m hos_ares_cli`，效果相同。

## 提交之前

**本地跑的和 CI 跑的是同一份定义** —— 清单在 `scripts/verify.py`，`.github/workflows/verify.yml`
调用的也是它。所以不要在别处再写一份命令列表。

**看长跑进度别用日志文件大小**：pytest 输出重定向到文件时是**块缓冲**，几十分钟不刷新是正常的，
进程其实在跑（B424 踩过：40 分钟只长 3 字节，被误判成挂死，还写进了账本）。
要么 `PYTHONUNBUFFERED=1`，要么加 `--junit-xml` 看产物；判断"跑完了"只看**退出码**，不看点了多少个。

**看闸门结果也别用管道**：`verify.py | tail -2 && git commit` 里那个 `&&` 判的是 `tail` 的退出码，
不是 `verify.py` 的——闸门红了照样提交、照样推（B441 实际发生过）。正确写法：

```bash
python scripts/verify.py > /tmp/v.log 2>&1; echo "exit=$?"; tail -3 /tmp/v.log
```

**改「谁都会读」的文件，多加一步**：`pyproject.toml`、`packaging/`、常量表这类东西动过之后，
跑 `verify.py --quick-tests`（12 项 + 2000 条单测，约 2 分钟）。不带 `--quick-tests` 的闸门只看 lint、
账本与结构——B439 改了 `pyproject` 的包声明，三条打包用例红了，而当时闸门 12/12 全绿。
`--with-tests` 是加上集成段的全量（本机约 1 小时），留给 CI。

```bash
python scripts/verify.py                 # 快闸门：mypy + 各道 lint + 账本 + 计划结构（约 1 分钟）
python scripts/verify.py --with-tests    # 再加三段 pytest（全量，本机约 1 小时）
```

**用哪个解释器**：仓库根若有 `.venv`，用它跑（`.venv/Scripts/python scripts/verify.py`）。
全局 python 往往缺 `playwright` / `mypy`，得到的是"环境红"而不是代码红（实测：同一个提交
换解释器 = 6 failed / 16 skipped ↔ 全绿）。`verify.py` 与 `hos-ares doctor` 第一行都会打印
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
- [ ] 不提交 `out/`、`.hos-ares/` 下的运行产物
- [ ] 改动了判定逻辑时，在 PR 描述里说明「哪些输入的行为变了」——这是纯函数内核，行为变化要可枚举

## 安全

这是安全工具。漏洞报告走 [SECURITY.md](SECURITY.md)，**不要开公开 issue**。
