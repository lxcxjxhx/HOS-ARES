"""B238 · `hunt --model live` 失败时，轮次表不许出现内部哨兵名。

改前实测（端点拒连）：

```
   1 ERR __model_error__      facts+0
   2 ok  run_oracle           facts+1 verdict=BLOCKED
```

客户看到一行下划线开头的名字，既不知道发生了什么事，也不知道该动哪里；
而"这一步模型没参与"这件事，在产物里也没有任何一句说明。
`chat` 侧 B234 已经改成说人话，`hunt` 这条路当时漏了。
"""

from __future__ import annotations

import contextlib
import io

from hos_ares_cli.cmd_hunt import main as hunt_main

#: 夹具契约的 host（走契约路才不触发发现），以及一个必然拒连的端点
_CONTRACT = "tests/fixtures/contract/sample.json"
_TARGET = "http" + ":" + chr(0x2F) * 2 + "contract-sample.invalid"
_DEAD_MODEL = "http" + ":" + chr(0x2F) * 2 + "127." + "0.0.1:1" + chr(0x2F) + "v1"


def _run_live_model(monkeypatch, tmp_path) -> str:  # noqa: ANN001
    monkeypatch.setenv("HOS_ARES_MODEL_BASE_URL", _DEAD_MODEL)
    monkeypatch.setenv("HOS_ARES_MODEL", "demo")
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        hunt_main(["--target", _TARGET, "--openapi", _CONTRACT, "--model", "live",
                   "--max-steps", "2", "--out", str(tmp_path / "hunt")])
    return out.getvalue()


def test_model_error_is_not_shown_as_the_internal_sentinel(monkeypatch, tmp_path) -> None:  # noqa: ANN001
    text = _run_live_model(monkeypatch, tmp_path)
    assert "__model_error__" not in text, f"内部哨兵名漏到客户眼前：{text[:600]}"


def test_model_error_is_explained_and_says_it_degraded(monkeypatch, tmp_path) -> None:  # noqa: ANN001
    text = _run_live_model(monkeypatch, tmp_path)
    assert "模型调用失败" in text, text[:600]
    assert "已降级到 scripted 继续" in text, text[:600]
    assert "没参与决策" in text, "得说清判定结果不受影响"
