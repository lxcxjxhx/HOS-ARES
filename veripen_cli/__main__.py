"""B82 · 统一 CLI 入口（`H24 B82`）。

一个词进产品：`veripen chat` / `veripen run` / `veripen platform`，
而不是记住九个 `python -m veripen_cli.cmd_xxx`。

**透传规则**：子命令拿到剩下的 argv 后**原样**交给对应 `main()`。
中间层不解析、不改写、不补默认值——一旦开始"帮你补参数"，
`--help` 与实际行为就会分叉，而且分叉点没人记得住。

不引 `click`/`typer`：零依赖纪律（离线包要能装进内网），`argparse` 够用。
"""

from __future__ import annotations

import sys
from typing import Callable, Sequence, cast

from ._io import setup_stdout

#: 子命令 → (模块路径, 一句话说明)
COMMANDS: tuple[tuple[str, str, str], ...] = (
    ("chat", "veripen_cli.cmd_chat", "交互式 agent 会话（说人话，计划 + 工具流 + diff）"),
    ("run", "veripen_cli.cmd_run", "端到端渗透：契约→oracle→capsule→报告"),
    ("discover", "veripen_cli.cmd_discover", "无契约发现：从首页攒面（不产判定）"),
    ("platform", "veripen_cli.cmd_platform", "企业编排：多资产 + 审批阻断 + 汇总报告"),
    ("hunt", "veripen_cli.cmd_hunt", "边跑边打印（人盯着看的那种）"),
    ("console", "veripen_cli.cmd_console", "只读控制台（11 个只读操作）"),
    ("agent", "veripen_cli.cmd_agent", "指令模式会话（白名单动词）"),
    ("tui", "veripen_cli.tui", "终端三面板（只读）"),
    ("doctor", "veripen_cli.cmd_doctor", "部署自检（24 项）"),
    ("parity", "veripen_cli.cmd_parity", "三对标总验收（5 面 × 22 项）"),
    ("playbook", "veripen_cli.cmd_playbook", "Playbook 资产库"),
    ("replay", "veripen_cli.cmd_replay", "证据重放"),
    # B220：这个模块一直都在，只有 `make coverage-matrix` 能摸到——
    # 设计文档写的是 `veripen coverage --matrix`，命令行里却没有这个子命令
    ("coverage", "veripen_cli.cmd_coverage", "覆盖矩阵（14 类 oracle × 标签，空类显式列出）"),
)


#: 有意**不做成子命令**的命令模块（每条写清理由，B220）。
#: 它们是内部脚手架（跑分 / 用例检查 / 插件往返 / 工作台冒烟），由 Makefile 调；
#: 放进 `veripen <名字>` 会让用户以为那是产品入口。
INTERNAL_MODULES: dict[str, str] = {
    "veripen_cli.cmd_bench_leads": "跑分脚手架：由 make bench-leads 调",
    "veripen_cli.cmd_cases": "用例契约检查：由 make cases CASES=contract 调",
    "veripen_cli.cmd_plugin_roundtrip": "插件往返自检：由 make plugin-roundtrip 调",
    "veripen_cli.cmd_workbench_smoke": "工作台冒烟：由 make workbench-smoke 调",
}


def _load(module_path: str) -> Callable[[Sequence[str] | None], int]:
    import importlib

    mod = importlib.import_module(module_path)
    fn = getattr(mod, "main", None)
    if not callable(fn):
        raise SystemExit(f"{module_path} 没有 main(argv)")
    return cast("Callable[[Sequence[str] | None], int]", fn)


def _usage() -> str:
    width = max(len(name) for name, _, _ in COMMANDS)
    lines = ["veripen —— 可验证渗透内核", "", "用法：veripen <子命令> [参数…]", "",
             "子命令："]
    for name, _, desc in COMMANDS:
        lines.append(f"  {name:<{width}}  {desc}")
    lines += ["", "每个子命令都支持 --help：veripen chat --help", ""]
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    # 一处钉死输出编码（B207）：Windows 控制台默认 cp1252/GBK，打印中文会
    # UnicodeEncodeError 直接把命令打死——B200（verify 脚本）与 B206（exe 启动）
    # 都是这个坑。放在分发处，所有子命令自动受益。
    setup_stdout()

    if not args or args[0] in ("-h", "--help", "help"):
        print(_usage())
        return 0

    if args[0] in ("-V", "--version"):
        import veripen_cli

        print(getattr(veripen_cli, "__version__", "0.0.0"))
        return 0

    name, rest = args[0], args[1:]
    table = {n: m for n, m, _ in COMMANDS}
    module_path = table.get(name)
    if module_path is None:
        print(f"未知子命令：{name}", file=sys.stderr)
        print(_usage(), file=sys.stderr)
        return 2

    # **原样透传**：不解析、不改写、不补默认值
    return int(_load(module_path)(rest) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
