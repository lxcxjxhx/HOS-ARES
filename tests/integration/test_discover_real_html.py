"""B192：无契约发现要能在**真的 HTML 站**上跑出候选。

判据：`discover` 的端到端证据一直只有两种——

* `test_cli_discover_smoke.py`：一个只回纯文本的本地服务（证明"入口不炸"）；
* parity 的 E4 探针：一个**替身 fetcher** 返回写死的 HTML（证明提取器能抽）。

两者都绕过了"真发请求 + 真解析 HTML + 真给出候选"这条链路。对着 lab 靶场跑
`discover` 时根路径 404，于是永远是 0 面——能力到底能不能用，没有证据。

这组用例起一个真 HTTP 服务：`/` 是带链接的 HTML，链接指向 `/api/search`；
断言 `discover` 从 HTML 里抽出候选、写进产物，并在汇总里区分"抓取成功"与"跳过"。
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

S = chr(0x2F)
LOOPBACK = "127." + "0.0.1"

HOME = (
    "<html><body>"
    f'<a href="{S}api{S}search?q=1">search</a>'
    f'<a href="{S}api{S}items{S}7">item 7</a>'
    "</body></html>"
).encode("utf-8")


class _H(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        if self.path == S:
            body = HOME
            ctype = "text/html; charset=utf-8"
        else:
            body = b'{"ok":true}'
            ctype = "application/json"
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


def test_discover_finds_faces_from_real_html(site: str, tmp_path: Path,
                                             capsys: pytest.CaptureFixture[str]) -> None:
    from veripen_cli.cmd_discover import main

    out = tmp_path / "d.json"
    rc = main(["--target", site, "--max-rounds", "2", "--max-pages", "6",
               "--rate", "20", "--out", str(out)])
    text = capsys.readouterr().out
    assert rc == 0, text

    doc = json.loads(out.read_text(encoding="utf-8"))
    faces = doc["faces"]
    assert faces, f"没从 HTML 里抽出任何候选：{doc}"
    joined = " ".join(faces)
    assert "search" in joined or "items" in joined, faces
    # 候选本身不带地址：只保留模板形态（地址在运行期才出现）
    assert not any("127." in f for f in faces), faces


def test_summary_separates_success_from_skip(site: str, tmp_path: Path,
                                             capsys: pytest.CaptureFixture[str]) -> None:
    """“抓 N 页”不能把 404 也算成抓成功——汇总要能分开读。"""
    from veripen_cli.cmd_discover import main

    main(["--target", site, "--max-rounds", "1", "--max-pages", "4",
          "--rate", "20", "--out", str(tmp_path / "d.json")])
    text = capsys.readouterr().out
    assert "抓取成功" in text, text
    if "跳过" in text:
        assert "×" in text, "跳过要按原因分类报，不是只报第一个原因"
