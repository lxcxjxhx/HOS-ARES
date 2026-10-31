"""B5 靶场夹具（放 conftest 才会被同目录测试自动发现）。

提供 ``Lab``（脆弱态/安全态可切换的进程内应用 + 传输 + 符号表）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterator

import pytest

from lab import routes as R
from lab.app import App, make_token
from lab.transport_oob import LabTransport
from veripen_oracles.common.harness import SymbolTable


@dataclass
class Lab:
    secure: bool
    app: App = field(init=False)
    transport: LabTransport = field(init=False)
    symbols: SymbolTable = field(init=False)
    token: str = field(init=False)

    def __post_init__(self) -> None:
        self.app = App(secure=self.secure)
        self.transport = LabTransport(self.app)
        self.symbols = SymbolTable(
            {
                "OOB": "oob-SAMPLE",
                # 含 scheme 的形态：scheme 由部署决定，写进符号值而不是代码
                "OOB_URL": "https" + "://" + "oob-SAMPLE",
                "DEAD_HOST": "none-SAMPLE",
                "STRANGER": "secondary",
                "OWNER": "primary",
                "SAFE_TARGET": "account/settings",
                "SAFE_FILE": "manual.txt",
                "SYSTEM_FILE": "etc/passwd",
                "SITE_HOST": "lab",
            }
        )
        self.token = make_token("secondary")

    def binding(self, tag: str):  # noqa: ANN201 - 绑定的具体类型由路由层拥有
        return R.binding_for(tag)


@pytest.fixture()
def vulnerable() -> Lab:
    # 每个用例一份全新的靶场：写操作类会改 app.state，夹具不能共用实例
    return Lab(secure=False)


@pytest.fixture()
def hardened() -> Lab:
    return Lab(secure=True)


@pytest.fixture(params=[False, True], ids=["vulnerable", "hardened"])
def both_modes(request: pytest.FixtureRequest) -> Iterator[Lab]:
    yield Lab(secure=bool(request.param))
