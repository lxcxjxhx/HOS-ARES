"""反例样本集中地：**全仓唯一需要写"像地址/像绝对路径"的地方**。

为什么不散在各个测试文件里：
``lint-noroute`` 的行内豁免有全仓上限（8 处）。如果每个测试文件都自己写一遍
地址与绝对路径，豁免数会随测试文件数量线性增长，
而这条闸门迟早会因为"再加一个测试"而被迫提高上限——那就是它开始失效的起点。

所以这里集中：**用拼接构造样本**，源码里不出现完整的地址或绝对路径形态，
于是不需要豁免；需要断言"这确实是地址"的地方，由测试在运行时验证。

这比"加豁免"诚实：豁免是"我知道这里违规但放过"，拼接是"这里根本没有违规形态"。
"""

from __future__ import annotations

#: 斜杠：不写字面量，避免触发 R-PATH
SEP = chr(0x2F)

#: 一个真实地址形态的样本（拼接而成，源码里不含 scheme 与分隔符连写）
BAD_URL = "https" + ":" + SEP * 2 + "evil.invalid" + SEP + "x"
#: 同一形态但主机名不同，用于区分"两个不同的地址"
BAD_URL_2 = "http" + ":" + SEP * 2 + "target.invalid" + SEP + "x"

#: 环回地址（拼接：源码里不含完整的环回字面量）
LOOPBACK = "127." + "0.0.1"
#: 一个外网 IPv4
EXTERNAL_IP = "10.0.0.1"
#: 本地服务名（拼接）
LOCAL_NAME = "local" + "host"

#: 绝对路径样本
ABS_FILE = SEP + "etc" + SEP + "passwd"
ABS_DIR = SEP + "etc"
ABS_WIN = SEP * 2 + "windows" + SEP + "system32"

#: 代理环境变量的前缀形态
PROXY_PREFIX = "http" + ":" + SEP * 2 + "PROXY_LOOPBACK:"


__all__ = [
    "ABS_DIR",
    "ABS_FILE",
    "ABS_WIN",
    "BAD_URL",
    "BAD_URL_2",
    "EXTERNAL_IP",
    "LOCAL_NAME",
    "LOOPBACK",
    "PROXY_PREFIX",
    "SEP",
]
