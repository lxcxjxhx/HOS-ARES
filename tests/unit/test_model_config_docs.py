"""B226：接真模型这件事必须能从命令行与 README 发现。

环境变量以前只在一张"未验清单"表格里出现过一次，`veripen chat --help` 一个字都没有——
有模型端点的客户找不到怎么接，等于 agent 能力在实践中停在规则层。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VARS = ("VERIPEN_MODEL_BASE_URL", "VERIPEN_MODEL", "VERIPEN_MODEL_API_KEY")


def _help(cmd: str) -> str:
    proc = subprocess.run(  # noqa: S603 - 固定 argv
        [sys.executable, "-m", "veripen_cli", cmd, "--help"],
        cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=180,
    )
    return proc.stdout + proc.stderr


def test_chat_help_says_how_to_attach_a_model() -> None:
    text = _help("chat")
    for var in VARS:
        assert var in text, f"chat --help 没提 {var}"


def test_agent_help_says_it_does_not_use_a_model() -> None:
    """指令模式不接模型——最容易被误解成"配了 env 就会用"。"""
    text = _help("agent")
    assert "不调模型" in text
    assert "VERIPEN_MODEL_BASE_URL" in text, "没告诉人该去 chat"


def test_readme_documents_the_model_vars_together() -> None:
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    for var in VARS:
        assert var in text, f"README 没写 {var}"
    assert "model: rules" in text, "没说明不配时会显示 rules"
