"""B258 · `hunt.json` 里也不许出现内部哨兵名（B238 的机器产物那一半）。

改前：`steps[].tool` 直接写 loop 的内部哨兵 `__model_error__`。终端那侧 B238 已改成人话，
但机器产物留着——下游按 tool 名做统计/告警的人被迫认一个下划线开头的实现细节。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_CONTRACT = "tests/fixtures/contract/sample.json"
_TARGET = "http" + ":" + chr(0x2F) * 2 + "contract-sample.invalid"
_DEAD_MODEL = "http" + ":" + chr(0x2F) * 2 + "127." + "0.0.1:1" + chr(0x2F) + "v1"


def _hunt(tmp_path: Path) -> dict:
    """用死端点跑一次 hunt（模型层必失败），返回 hunt.json。"""
    env = {
        "HOS_ARES_MODEL_BASE_URL": _DEAD_MODEL,
        "HOS_ARES_MODEL": "demo",
        "PYTHONIOENCODING": "utf-8",
        "PATH": str(Path(sys.executable).parent),
        "SYSTEMROOT": str(Path("C:/Windows")) if Path("C:/Windows").exists() else "",
    }
    subprocess.run(
        [sys.executable, "-m", "hos_ares_cli", "hunt", "--target", _TARGET,
         "--openapi", _CONTRACT, "--model", "live", "--max-steps", "2",
         "--out", str(tmp_path / "h")],
        cwd=str(ROOT), env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=180,
    )
    return json.loads((tmp_path / "h" / "hunt.json").read_text(encoding="utf-8"))


def test_hunt_json_does_not_leak_the_internal_sentinel(tmp_path: Path) -> None:
    record = _hunt(tmp_path)
    raw = json.dumps(record, ensure_ascii=False)
    assert "__model_error__" not in raw, "机器产物里漏了内部哨兵名"


def test_hunt_json_uses_the_public_name_for_a_model_failure(tmp_path: Path) -> None:
    record = _hunt(tmp_path)
    tools = [s["tool"] for s in record["steps"]]
    assert "model_error" in tools, tools
    step = next(s for s in record["steps"] if s["tool"] == "model_error")
    assert step["ok"] is False
    assert step["error_class"] in {"transport", "schema"}, step
