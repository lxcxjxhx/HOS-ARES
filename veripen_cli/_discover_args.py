"""发现相关参数的唯一定义处（B113）。"""

from __future__ import annotations

import argparse  # noqa: F401  （仅用于类型注解）


def add_discovery_args(
    ap: "argparse.ArgumentParser",
    *,
    include_enable: bool = True,
    legacy_aliases: bool = False,
) -> None:
    """发现相关的命令行参数：**只在这里定义一次**。

    这组参数曾经在 discover / run / hunt 三个入口各抄一遍，而且已经分叉：
    hunt 少了 ``--submit-forms``；discover 的轮次数叫 ``--max-rounds``。
    命令行不会因此报错，只是能力悄悄少一块——所以收成一份。

    ``legacy_aliases``：给 ``veripen discover`` 保留 ``--max-rounds/--max-pages``
    两个旧拼写（同一个 dest），旧脚本不用改。
    """
    if include_enable:
        ap.add_argument(
            "--discover",
            action="store_true",
            help="无契约：先从目标攒面再跑（与 --openapi 二选一）",
        )
    rounds = ("--discover-rounds", "--max-rounds") if legacy_aliases else ("--discover-rounds",)
    pages = ("--discover-pages", "--max-pages") if legacy_aliases else ("--discover-pages",)
    ap.add_argument(*rounds, dest="discover_rounds", type=int, default=2, help="发现最多几轮")
    ap.add_argument(*pages, dest="discover_pages", type=int, default=20, help="每轮最多抓几页")
    ap.add_argument(
        "--render",
        action="store_true",
        help="发现时渲染 HTML（补 JS 生成的入口与同源 XHR；慢，默认关）",
    )
    ap.add_argument(
        "--submit-forms",
        action="store_true",
        help="提交无副作用的 GET 表单（探针值 1），看响应里还有什么",
    )


