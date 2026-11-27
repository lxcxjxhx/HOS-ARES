"""`patch` 工具在 chat 里的可达性验证（B606）。

## 为什么单独验

规则模式**故意不解析 `patch`**（B604：猜错的写操作会改坏文件）。
但 `patch` 本身在 `cmd_chat.py:404` 是实现了的 —— 它走**模型层**：
模型给出结构化 `patches` 数组，`_exec_step` 执行。

这条链路此前没有用例覆盖。`patch` 是**多文件批量改**的唯一入口，
不通就等于"多文件编辑"这个能力在 chat 里不存在。
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
    env = {**os.environ, **env_extra}
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


def test_patch_reaches_two_files_in_one_step(tmp_path: Path) -> None:
    """**多文件编辑的唯一入口**：一次 `patch` 调用改两个文件。"""
    from lab.model_server import ModelServer

    (tmp_path / "a.txt").write_text("hello\nworld\n", encoding="utf-8")
    (tmp_path / "b.txt").write_text("foo\nbar\n", encoding="utf-8")

    srv = ModelServer(
        mode="ok",
        tool="patch",
        args={"patches": [
            {"path": "a.txt", "old": "hello", "new": "HI"},
            {"path": "b.txt", "old": "foo", "new": "FOO"},
        ]},
    ).start()
    try:
        code, out = _run_chat(
            tmp_path,
            {"HOS_ARES_MODEL_BASE_URL": srv.base_url, "HOS_ARES_MODEL": "local"},
            "把 a.txt 和 b.txt 都改一下\nquit\n",
        )
    finally:
        srv.stop()

    assert code == 0, out[-500:]
    assert (tmp_path / "a.txt").read_text(encoding="utf-8") == "HI\nworld\n", out[-400:]
    assert (tmp_path / "b.txt").read_text(encoding="utf-8") == "FOO\nbar\n", out[-400:]


def test_patch_with_a_missing_old_value_fails_loudly(tmp_path: Path) -> None:
    """`old` 找不到 → **该失败就失败**，不许静默当成改成功。"""
    from lab.model_server import ModelServer

    (tmp_path / "x.txt").write_text("actual\n", encoding="utf-8")
    srv = ModelServer(
        mode="ok",
        tool="patch",
        args={"patches": [{"path": "x.txt", "old": "not-there", "new": "y"}]},
    ).start()
    try:
        code, out = _run_chat(
            tmp_path,
            {"HOS_ARES_MODEL_BASE_URL": srv.base_url, "HOS_ARES_MODEL": "local"},
            "改 x.txt\nquit\n",
        )
    finally:
        srv.stop()

    # 文件内容不能被动过
    assert (tmp_path / "x.txt").read_text(encoding="utf-8") == "actual\n"
    # 会话要么报失败、要么明确说不懂 —— 不能安静地"成功"
    assert ("失败" in out) or ("找不到" in out) or (code != 0), out[-600:]
