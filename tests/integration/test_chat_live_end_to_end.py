"""端到端验证 `chat --model live`：**模型层真的把工具调用跑通了**。

## 这一层此前没验过

B554 验的是**传输层**的四种失败形态（断流 / usage 分片 / 首包延迟 / 网关错误码）。
`from_env()` → `HttpChatClient` → `_plan_from_model` → `_exec_step` 这条**完整链路**
只在假端点返回固定 JSON 时跑过一次（`test_chat_model.py` 的 11 条），
但那些用例**没有真的跑 CLI**。

本批起一个真的 HTTP 假端点，用 `HOS_ARES_MODEL_BASE_URL` /
`HOS_ARES_MODEL` 两个环境变量接进去，跑**真的 `hos-ares chat` 子进程**，
看它有没有真的把文件写出来。

## 为什么这比「需要真模型 key」更有价值

真模型能验的是「模型会不会守窄 schema」——那是模型能力，不是我们的产品。
**「模型给了合规调用之后，我们的链路能不能把它跑通」**是我们能验的，
而且失败时成本最低（不用等 model 回包 30 秒）。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for extra in (ROOT, ROOT / "tests"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))


def _run_chat(workdir: Path, env_extra: dict[str, str], stdin_text: str) -> tuple[int, str]:
    """起一个真的 chat 子进程。"""
    env = {**os.environ, **env_extra}
    # 清掉可能干扰的继承变量
    for key in ("HOS_ARES_MODEL_BASE_URL", "HOS_ARES_MODEL", "HOS_ARES_MODEL_STREAM"):
        if key not in env_extra:
            env.pop(key, None)
    proc = subprocess.run(
        [sys.executable, "-m", "hos_ares_cli", "chat",
         "--root", str(workdir), "--yes"],
        input=stdin_text, capture_output=True, text=True,
        encoding="utf-8", errors="replace", cwd=str(ROOT), env=env, timeout=180,
    )
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def test_live_model_writes_the_file_it_was_asked_to_write(tmp_path: Path) -> None:
    """**完整链路**：环境变量 → 真 endpoint → 模型给 `write` 调用 → 文件真被写出来。

    这条用例的价值：它验的是「我们的链路」，不是「模型的能力」。
    """
    from lab.model_server import ModelServer

    srv = ModelServer(
        mode="ok",
        tool="write",
        args={"path": "live.txt", "content": "hello-from-live"},
    ).start()
    try:
        code, out = _run_chat(
            tmp_path,
            {"HOS_ARES_MODEL_BASE_URL": srv.base_url, "HOS_ARES_MODEL": "local"},
            "写一个文件\nquit\n",
        )
    finally:
        srv.stop()

    assert code == 0, out[-400:]
    target = tmp_path / "live.txt"
    assert target.exists(), f"文件没被写出来：\n{out[-600:]}"
    assert target.read_text(encoding="utf-8") == "hello-from-live"


def test_live_model_call_reaches_the_endpoint(tmp_path: Path) -> None:
    """端点**真的被调到了**（不只是降级成 rules 也能写文件）。"""
    from lab.model_server import ModelServer

    srv = ModelServer(mode="ok", tool="write",
                      args={"path": "x.txt", "content": "y"}).start()
    try:
        code, out = _run_chat(
            tmp_path,
            {"HOS_ARES_MODEL_BASE_URL": srv.base_url, "HOS_ARES_MODEL": "local"},
            "写 x.txt\nquit\n",
        )
        requests = srv.requests
    finally:
        srv.stop()

    assert code == 0, out[-400:]
    assert requests, "端点一次都没被调用——那说明不是 live 模式"
    # 请求里要带上 model 名与工具清单（窄 schema 的载体）
    assert requests[0]["model"] == "local", requests[0]
    assert requests[0]["has_tools"], "请求里没有工具清单"


def test_live_model_bad_response_degrades_but_still_works(tmp_path: Path) -> None:
    """模型给不合规响应 → 降级 rules，**会话不崩**，仍能把事做完。

    这是「有模型但模型不听话」的现实场景：产品必须能继续干活。
    """
    from lab.model_server import ModelServer

    srv = ModelServer(mode="prose").start()          # 说人话，不给 JSON
    try:
        code, out = _run_chat(
            tmp_path,
            {"HOS_ARES_MODEL_BASE_URL": srv.base_url, "HOS_ARES_MODEL": "local"},
            "写一个 degrade.txt\nquit\n",
        )
    finally:
        srv.stop()

    assert code == 0, out[-400:]
    assert "降级" in out or "rules" in out, f"没有降级提示：\n{out[-500:]}"
    assert (tmp_path / "degrade.txt").exists(), f"降级后 rules 也该把事做完：\n{out[-500:]}"


def test_without_env_vars_it_stays_in_rules_mode(tmp_path: Path) -> None:
    """**没配环境变量就不假装有模型**——这是 `from_env` 的关键契约。"""
    code, out = _run_chat(tmp_path, {}, "写一个 rules.txt\nquit\n")
    assert code == 0, out[-400:]
    assert (tmp_path / "rules.txt").exists()
