"""`veripen` CLI（`24 §5` 的 make 目标背后就是这些子命令）。

B4 只交付 `replay`；其余子命令按批次加。
"""

from __future__ import annotations

import sys
from typing import Optional

import typer

app = typer.Typer(
    name="veripen",
    help="HOS-ARES VeriPen — 可审计、可复现、可离线部署的渗透验证内核",
    no_args_is_help=True,
    add_completion=False,
)


@app.command()
def version() -> None:
    """打印版本。"""
    from veripen_core import __version__

    typer.echo(__version__)


@app.command()
def replay(
    capsule_path: str = typer.Argument(..., help="capsule JSON 文件路径"),
    profile: Optional[str] = typer.Option(None, "--profile", help="当前 profile 名（用于 stale 检测）"),
    dry_run: bool = typer.Option(False, "--dry-run", help="不发包，只校验链路与哈希"),
    offline_fixture: bool = typer.Option(
        False, "--offline-fixture", help="用 capsule 内保存的响应重放判定函数（断网可用）"
    ),
    json_out: bool = typer.Option(False, "--json", help="机器可读输出"),
) -> None:
    """复现一条发现（A4 断言）：`make replay ID=n` 的背后。

    四态输出（`06 §5.1`），不要只说成功/失败：
      REPLAYED_OK / REPLAYED_PARTIAL / ASSERTION_NOT_REPRODUCED / REPLAY_BLOCKED
    """
    from veripen_core.errors import VeripenError
    from veripen_core.evidence import CapsuleSealer, ReplayEngine, ReplayMode, ReplayStatus

    sealer = CapsuleSealer()

    try:
        capsule = sealer.load(capsule_path)
    except VeripenError as exc:
        _emit(json_out, {"status": "ERROR", "code": exc.code, "message": exc.message})
        raise typer.Exit(code=2) from exc

    if dry_run:
        mode = ReplayMode.DRY_RUN
    elif offline_fixture:
        mode = ReplayMode.OFFLINE_FIXTURE
    else:
        mode = ReplayMode.LIVE

    engine = ReplayEngine()
    outcome = engine.run(capsule, mode=mode)

    payload = {
        "status": outcome.status.value,
        "verdict": outcome.verdict.value if outcome.verdict else None,
        "reason": outcome.reason,
        "steps_resent": outcome.steps_resent,
        "notes": list(outcome.notes),
        "capsule_id": capsule.capsule_id(),
        "finding_id": capsule.finding_id,
        "self_sha256": capsule.self_sha256(),
        "is_security_block": outcome.status is ReplayStatus.REPLAY_BLOCKED,
    }
    _emit(json_out, payload)

    # 退出码：OK/PARTIAL/BLOCKED 都是"正常完成"（BLOCKED 是安全行为）
    if outcome.status is ReplayStatus.ASSERTION_NOT_REPRODUCED:
        raise typer.Exit(code=1)
    raise typer.Exit(code=0)


@app.command("verify-chain")
def verify_chain_cmd(
    capsule_paths: list[str] = typer.Argument(..., help="按顺序给出 capsule 文件"),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """顺序校验证据链（`06 §3`）。任何一条被改即断链并定位。"""
    from veripen_core.evidence import CapsuleSealer, verify_chain

    sealer = CapsuleSealer()
    caps = [sealer.load(p) for p in capsule_paths]
    result = verify_chain(caps)
    _emit(json_out, result)
    raise typer.Exit(code=0 if result["ok"] else 1)


def _emit(as_json: bool, payload: dict) -> None:
    if as_json:
        import json

        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    for line in payload.get("notes") or []:
        typer.echo(f"  note: {line}")
    typer.echo(f"  status: {payload.get('status')}")
    if payload.get("verdict"):
        typer.echo(f"  verdict: {payload['verdict']}")
    typer.echo(f"  reason: {payload.get('reason')}")


def main() -> int:  # pragma: no cover
    app()
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
