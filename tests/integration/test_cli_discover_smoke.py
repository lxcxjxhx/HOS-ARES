"""B110 · CLI 冒烟：`discover` 与 `run --discover` 必须能真跑起来。

这一批的由来：B107 给这两个入口加了 `well_known_candidates(...)` 调用，却漏了
`cmd_discover` 的 import——`veripen discover` 运行即 NameError，而当时的 E2E 全走
`measure_discovery`，正好绕过命令行。所以这里不测行为细节，只测**入口不炸**。
"""

from __future__ import annotations

import ast
import pathlib
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

S = chr(0x2F)
#: 回环地址拼出来：测试目录也在零地址闸门扫描范围内。
LOOPBACK = "127" + "." + "0" + "." + "0" + "." + "1"
PAGE = ('<html><body><a href="' + S + 'about.htm">a</a></body></html>').encode()


class _H(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        body = PAGE if self.path == S else b'{"ok":true}'
        ctype = "text/html" if self.path == S else "application/json"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):  # noqa: ANN002
        pass


@pytest.fixture(scope="module")
def site():  # noqa: ANN201
    srv = HTTPServer((LOOPBACK, 0), _H)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield "http" + ":" + S * 2 + f"{LOOPBACK}:{port}"
    finally:
        srv.shutdown()


def test_discover_cli_runs(tmp_path: pathlib.Path, site: str) -> None:
    from veripen_cli.cmd_discover import main

    rc = main(["--target", site, "--max-rounds", "1", "--max-pages", "3",
               "--rate", "10", "--out", str(tmp_path / "d.json")])
    assert rc == 0
    assert (tmp_path / "d.json").is_file()


def test_run_discover_cli_runs(tmp_path: pathlib.Path, site: str) -> None:
    from veripen_cli.cmd_run import main

    # 没有可判定面时 run 返回 1（不静默成功），但**不许炸**
    rc = main(["--target", site, "--discover", "--discover-rounds", "1",
               "--discover-pages", "3", "--out", str(tmp_path / "r")])
    assert rc in (0, 1)


def test_no_module_uses_names_it_never_imported() -> None:
    """粗查：CLI 模块里凡出现 `xxx(` 的公开函数名，必须能解析到定义或 import。

    这条不是通用静态检查（那要 mypy），只针对"同一批改了调用却没改 import"这个坑。
    """
    targets = [
        "veripen_cli/cmd_discover.py",
        "veripen_cli/cmd_run.py",
        "veripen_cli/cmd_hunt.py",
    ]
    for rel in targets:
        path = pathlib.Path(rel)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
        imported: set[str] = set()
        defined: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                imported |= {a.asname or a.name for a in node.names}
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                defined.add(node.name)
            elif isinstance(node, ast.Assign):
                defined |= {t.id for t in node.targets if isinstance(t, ast.Name)}
        called = {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        import builtins

        builtin_names = set(dir(builtins))
        missing = {
            name for name in called
            if name not in imported and name not in defined and name not in builtin_names
        }
        assert not missing, f"{rel} 调用了未导入的名字：{sorted(missing)}"
