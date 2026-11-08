"""B149 · 多面之后链还连得上，且照样能认出篡改。

B148 把 capsule 从「每个 oracle 一份」改成「每个面一份」，链变长。
链是证据的地基——如果它连得上但认不出乱序/缺条，"整轮不可篡改"就是空话。

这组用例把七件事钉住：
1. 有且只有一个 genesis（`prev = 64 个 0`）；
2. 每条 `prev` 都能在盘上找到，顺链能走完全部；
3. 顺链 `verify_chain` 通过，**逆序、缺条、删链头必须失败**；
4. 每份 capsule 的 `route.ref` 都能在 findings 里找到对应的面；
5. 报告列出的证据全集 == 盘上的 capsule 全集（链尾缺口由这个集合对账）；
6. 报告头的 `root_sha256` == 链头（`verify-chain` 的 `head=`），链长与证据份数一致；
7. `verify_chain` 报出链长与链尾，多面之后"7 份"要真的是 7 环。
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

S = chr(0x2F)
LOOPBACK = "127" + "." + "0" + "." + "0" + "." + "1"
HTTP = "http" + ":" + S * 2
ZERO = "0" * 64

HOME = (
    '<html><body><a href="' + S + 'a?x=1">a</a>'
    '<a href="' + S + 'b?x=1">b</a></body></html>'
).encode()


class _H(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        body, ctype = b'{"total":0,"items":[]}', "application/json"
        if not (self.path.startswith(S + "a") or self.path.startswith(S + "b")):
            body, ctype = HOME, "text/html"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):  # noqa: ANN002
        pass


@pytest.fixture(scope="module")
def run_dir(tmp_path_factory: pytest.TempPathFactory):  # noqa: ANN201
    out = tmp_path_factory.mktemp("chain") / "run"
    srv = HTTPServer((LOOPBACK, 0), _H)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        from veripen_cli.cmd_run import main

        main([
            "--target", HTTP + f"{LOOPBACK}:{port}",
            "--discover", "--discover-rounds", "2", "--discover-pages", "8",
            "--out", str(out),
        ])
    finally:
        srv.shutdown()
    return out


def _chain(run_dir) -> list:  # noqa: ANN001
    from veripen_core.evidence import load_capsule

    caps = [load_capsule(str(f)) for f in sorted((run_dir / "capsules").rglob("*.json"))]
    by_self = {c.chain.self_sha256: c for c in caps}
    genesis = [c for c in caps if c.chain.prev_sha256 in ("", ZERO)]
    assert len(genesis) == 1, f"genesis 应唯一，实际 {len(genesis)}"

    order, seen, cur = [], set(), genesis[0]
    while cur is not None and cur.chain.self_sha256 not in seen:
        order.append(cur)
        seen.add(cur.chain.self_sha256)
        nxt = [c for c in caps if c.chain.prev_sha256 == cur.chain.self_sha256]
        cur = nxt[0] if nxt else None
    assert len(order) == len(caps), f"顺链只走了 {len(order)}/{len(caps)}——有断链"
    for cap in caps:
        assert cap.chain.self_sha256 in by_self
    return order


def test_chain_is_intact(run_dir) -> None:  # noqa: ANN001
    from veripen_core.evidence import verify_chain

    order = _chain(run_dir)
    assert len(order) >= 2, "多面才有意义"
    assert verify_chain(order).ok, "顺链校验必须通过"


def test_reversed_chain_is_rejected(run_dir) -> None:  # noqa: ANN001
    from veripen_core.evidence import verify_chain

    order = _chain(run_dir)
    assert not verify_chain(list(reversed(order))).ok, "逆序必须被拒"


def test_chain_with_gap_is_rejected(run_dir) -> None:  # noqa: ANN001
    from veripen_core.evidence import verify_chain

    order = _chain(run_dir)
    assert not verify_chain(order[:1] + order[2:]).ok, "缺条必须被拒"


def test_removed_head_is_rejected(run_dir) -> None:  # noqa: ANN001
    """删掉链头（=少一份证据）不能还判 ok。

    剩下的条 `prev` 两两对得上，只查"中间没断"会让整根链自洽：
    少了一份证据，校验器却说"完整"——这比报错更糟。
    """
    from veripen_core.evidence import verify_chain

    order = _chain(run_dir)
    report = verify_chain(order[1:])
    assert not report.ok, "删链头必须被拒"
    assert any("链头" in b for b in report.breaks), report.breaks


def test_report_root_is_chain_head(run_dir) -> None:  # noqa: ANN001
    """报告头的 `root_sha256` 必须就是链头：两个出口（报告 / verify-chain）逐字对上。"""
    from veripen_core.evidence import verify_chain

    order = _chain(run_dir)
    report = verify_chain(order)
    doc = json.loads((run_dir / "findings.json").read_text(encoding="utf-8"))
    assert doc["root_sha256"] == report.head_sha256
    assert report.length == len(order)


def test_report_evidence_set_matches_disk(run_dir) -> None:  # noqa: ANN001
    """报告说"证据 N 份"就必须是盘上的 N 份。

    链尾被删时链本身无法自证（没有外部锚点），所以尾巴由**报告列出的证据全集**对账：
    两份集合必须逐一对上，谁少了都读得出来。
    """
    order = _chain(run_dir)
    doc = json.loads((run_dir / "findings.json").read_text(encoding="utf-8"))
    reported = [f for f in doc["findings"] if f.get("outcome") == "reported"]
    assert reported, "这次运行没有可报告结论，用例失去意义"
    in_report = {str((f.get("evidence") or {}).get("capsule_id")) for f in reported}
    on_disk = {f"sha256:{c.chain.self_sha256}" for c in order}
    assert in_report == on_disk, f"报告多 {in_report - on_disk}／少 {on_disk - in_report}"


def test_every_capsule_maps_to_a_face(run_dir) -> None:  # noqa: ANN001
    order = _chain(run_dir)
    doc = json.loads((run_dir / "findings.json").read_text(encoding="utf-8"))
    refs = {(f["oracle"], (f.get("route") or {}).get("ref")) for f in doc["findings"]}
    orphans = [(c.oracle.id, c.route.ref) for c in order if (c.oracle.id, c.route.ref) not in refs]
    assert not orphans, f"这些 capsule 在 findings 里找不到对应的面：{orphans}"
