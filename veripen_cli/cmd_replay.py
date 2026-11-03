#!/usr/bin/env python3
"""veripen replay —— A4「不是我说有洞」的命令行形态（`06 §5`）。

用法：
    python -m veripen_cli.cmd_replay --capsule out/b12/capsules/<id>.json --out out/replay
    python -m veripen_cli.cmd_replay --capsule X --mode dry        # 预检，不发包
    python -m veripen_cli.cmd_replay --selftest                    # 反向证明：篡改必须 TAMPERED

三件事，每件都必须**真的发生**，不许用替身：

1. **完整性**：`assert_self_sha` 先跑。改一个字节必须得 `TAMPERED`，
   而不是"重放通过"——这条就是 A4 的实质。
2. **重放**：`LIVE` 模式必须真发包（`NetTransport`），`DRY` 只跑前三道门。
   `--selftest` 会临时翻转 capsule 里一个字节，证明闸门能红。
3. **报告**：输出六态 + 每步的 (role, status) 对照，人可读。

**为什么单独做成命令**：能力一直在 `veripen_core/evidence.py` 里，
但没有任何入口能让人自己跑一次——于是"A4 成立"只能靠引用别人的测试输出。
断言必须能被第三方复跑，否则它不是断言，是宣称。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ._io import setup_stdout  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
for extra in (ROOT, ROOT / "tests"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from veripen_core.evidence import CapsuleDoc, ReplayResult  # noqa: E402
from veripen_core.models import ReplayStatus  # noqa: E402
from veripen_core.evidence import (  # noqa: E402
    CapsuleError,
    CapsuleTampered,
    ReplayEnv,
    ReplayMode,
    assert_self_sha,
    load_capsule,
    loads_capsule,
)

#: 七态的中文说明（`06 §5`；`models.ReplayStatus` 是唯一真源）。
#: 「OK」不等于「漏洞还在」，「PARTIAL」也不等于「修好了」——
#: 这条区分是 `06 §5.2` 的核心，命令行必须把它讲出来，否则读者只会看绿/红。
STATE_MEANING = {
    "NEVER": "DRY 预检：只算「将要发什么」，未发包",
    "OK": "逐字节复现（强证据：结论与细节都相同）",
    "PARTIAL": "状态形状对但细节有差（**不能**读成「漏洞还在」，也不能读成「已修复」）",
    "FAILED": "结论不成立（这次没复现）",
    "BLOCKED": "被门挡住（scope/授权/计划），没有发包",
    "ROUTE_UNRESOLVED": "路由门：capsule 的 ref 在当前环境解析不到（**不是漏洞没了**）",
    "ROUTE_DRIFT": "路由门：ref 还在但模板变了（同一个面被改过）",
}


def _fail(msg: str) -> int:
    setup_stdout()
    print(f"replay: FAIL: {msg}")
    return 1


def _tamper_probe(cap: CapsuleDoc, out: Path) -> int:
    del out
    """把 capsule 的一个字节改掉，断言必须 TAMPERED。

    这是 A4 真正的证明：如果篡改后仍然「重放通过」，那这条命令就毫无意义。
    改动落在 body_sha256 上（payload 里的一个 hex 字符），既不破坏 JSON 结构，
    也必然改变自哈希——所以它测的是**哈希闸门**，不是解析容错。
    """
    doc = cap.model_dump(mode="json")
    steps = doc.get("steps") or []
    if not steps:
        return _fail("capsule 没有步骤，无法做篡改证明")
    resp = steps[0].get("response") or {}
    sha = str(resp.get("body_sha256") or "")
    if not sha:
        return _fail("第一步没有 body_sha256，无法做篡改证明")
    # 翻转第一个 hex 字符：'0'->'1'，其它 -> '0'
    resp["body_sha256"] = ("1" if sha[0] == "0" else "0") + sha[1:]
    raw = json.dumps(doc, ensure_ascii=False).encode("utf-8")
    try:
        bad = loads_capsule(raw)
        assert_self_sha(bad)
    except CapsuleTampered:
        print("selftest: OK —— 篡改 1 字节后 assert_self_sha 抛 CapsuleTampered（闸门能红）")
        return 0
    except CapsuleError as exc:
        # 结构不完整也算挡住了，但要说清是哪一类，别把"解析失败"冒充"哈希闸门生效"
        print(f"selftest: PARTIAL —— 篡改后被 {type(exc).__name__} 挡住（不是哈希闸门）：{exc}")
        return 1
    return _fail("篡改 1 字节后仍通过自哈希校验——A4 不成立")


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="veripen replay")
    ap.add_argument("--capsule", help="capsule JSON 路径")
    ap.add_argument("--out", default="out/replay", help="输出目录")
    ap.add_argument("--mode", choices=["live", "dry", "offline"], default="live")
    ap.add_argument("--selftest", action="store_true", help="只做篡改证明（不需要靶场）")
    args = ap.parse_args(argv)

    if args.selftest:
        if not args.capsule:
            return _fail("--selftest 需要 --capsule")
        try:
            cap = load_capsule(args.capsule)
        except CapsuleError as exc:
            return _fail(f"capsule 载入失败：{exc}")
        return _tamper_probe(cap, Path(args.out))

    if not args.capsule:
        return _fail("需要 --capsule（或用 --selftest）")

    try:
        cap = load_capsule(args.capsule)
        assert_self_sha(cap)
    except CapsuleTampered as exc:
        return _fail(f"TAMPERED：capsule 自哈希不符（{exc}）")
    except CapsuleError as exc:
        return _fail(f"capsule 不可用：{exc}")

    mode = {"live": ReplayMode.LIVE, "dry": ReplayMode.DRY, "offline": ReplayMode.OFFLINE}[args.mode]
    if mode is ReplayMode.OFFLINE:
        return _fail("offline 模式需要 capsule 内的响应夹具，本命令只跑 live/dry；"
                     "离线重放见 tests/unit/test_evidence.py::test_offline_fixture")
    if mode is ReplayMode.LIVE:
        # LIVE 必须真发包。没有靶场就不许"假装跑过"——fail-closed（06 §5 fail-closed 原则）。
        return _fail(
            "live 模式需要真实靶场与 transport 注入，本命令不做自建靶场。"
            "用它做 dry 预检，或按 scripts/demo.py 的 step5 接 LabTransport 后调用 replay()。"
        )

    # ── DRY：不发包，只给「将要发什么」
    from veripen_core.routes.types import RouteSet

    # 空 RouteSet 是刻意的：DRY 只跑完整性/授权两道门，路由门必然给出
    # ROUTE_UNRESOLVED——这正好说明"没有绑定就不许发包"，而不是拿假绑定糊过去。
    rs = RouteSet(
        resolver_version="cli",
        asset_id="cli",
        query_hash="cli",
        created_at=0,
        expires_at=0,
        sources_used=(),
        sources_failed=(),
        unresolved=(),
        bindings=[],
    )
    env = ReplayEnv(now=0, mode=ReplayMode.DRY, routeset=rs)
    result = _replay(cap, env)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    record = {
        "capsule": args.capsule,
        "mode": args.mode,
        "state": result.state.value,
        "reason": result.reason,
        "meaning": STATE_MEANING.get(result.state.value, ""),
        "would_send": [list(x) for x in result.would_send],
    }
    (out_dir / "replay.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    setup_stdout()
    print(f"capsule : {Path(args.capsule).name}")
    print(f"id      : {cap.capsule_id[:16]}")
    print(f"mode    : {args.mode}    state: {result.state.value}")
    print(f"含义    : {STATE_MEANING.get(result.state.value, '')}")
    if result.reason:
        print(f"原因    : {result.reason}")
    for n, role, ref in result.would_send:
        print(f"  would_send[{n}] role={role} ref={ref}")
    print(f"证据    : {out_dir / 'replay.json'}")
    # DRY 不是"通过"：它只证明前三道门过得去。退出码 0 表示命令成功，不表示复现成功。
    return 0


def _replay(cap: CapsuleDoc, env: ReplayEnv) -> ReplayResult:
    from veripen_core.evidence import replay

    return replay(cap, env, None)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
