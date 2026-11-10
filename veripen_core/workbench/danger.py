"""B214 · 危险命令闸门（`sh` 动词的唯一守门人）。

**为什么要它**：`chat` / `agent` 的 `sh` 动词是唯一能跳出工作台的出口——
读写动词有 `Workbench` 的根约束，`sh` 没有：它跑什么就是什么，用的是当前账号的权限。
"计划先于执行"只解决"我知道要干什么"，不解决"这一条该不该干"。

**这条闸门的口径**（宁可拦宽，放行要显式）：

* 拦的是**不可逆或越界**：整盘/整目录删除、分区与磁盘写入、关机重启、注册表删除、
  防火墙清空、权限全开、凭据读取；
* 拦的是**把远程内容直接执行**：`curl … | sh`、`iwr … | iex`、`powershell -enc`；
* **不拦**渗透本身：`nmap`、`sqlmap`、`ffuf`、`curl` 打靶场、`python` 跑脚本都放行——
  这是产品要做的事，拦它等于把产品拦掉。

**放行方式只有一种**：调用方显式传 `allow_dangerous=True`（CLI 的 `--allow-dangerous`）。
默认关，且拒绝会被记成一步（`ok=False`），所以"拦了什么"在账本里查得到。
"""

from __future__ import annotations

import re
from typing import NamedTuple


class Danger(NamedTuple):
    pattern: re.Pattern[str]
    name: str
    why: str


#: 顺序无关（全表扫一遍，命中即拦）；每条都要能说清"为什么危险"
DANGEROUS: tuple[Danger, ...] = (
    # ── 整盘 / 整目录删除（不可逆）──────────────────────────────
    Danger(re.compile(r"\brm\s+(-[A-Za-z]*[rf][A-Za-z]*\s+)+(?:/|~|\$HOME|\*)\s*$"),
           "rm-root", "删根/家目录/通配：不可逆，且删的不只是工作根"),
    Danger(re.compile(r"\brm\s+-[A-Za-z]*r[A-Za-z]*f?\s+[A-Za-z]:[\\/]\s*$", re.I),
           "rm-drive", "删整个盘符"),
    Danger(re.compile(r"\b(?:del|erase)\s+/[fsq]\b.*\*", re.I),
           "del-wildcard", "Windows 通配强删"),
    Danger(re.compile(r"\b(?:rd|rmdir)\s+/s\b", re.I),
           "rmdir-recursive", "Windows 递归删目录"),
    # ── 分区 / 磁盘 / 引导 ─────────────────────────────────────
    Danger(re.compile(r"\bmkfs(?:\.\w+)?\b", re.I), "mkfs", "格式化文件系统"),
    Danger(re.compile(r"\bdd\b.*\bof\s*=\s*(?:/dev/|[A-Za-z]:)", re.I),
           "dd-disk", "直接写块设备/磁盘"),
    Danger(re.compile(r"\b(?:fdisk|parted|diskpart)\b", re.I), "partition", "改分区表"),
    Danger(re.compile(r"\b(?:shutdown|reboot|halt|poweroff)\b", re.I),
           "power", "关机/重启：会打断正在跑的验证"),
    # ── 系统配置（改了要重装）──────────────────────────────────
    Danger(re.compile(r"\breg\s+delete\b", re.I), "reg-delete", "删注册表键"),
    Danger(re.compile(r"\biptables\s+-F\b|\bnft\s+flush\b", re.I), "fw-flush", "清空防火墙规则"),
    Danger(re.compile(r"\bchmod\s+(?:-R\s+)?777\s+/(?:\s|$)", re.I), "chmod-root", "根目录权限全开"),
    Danger(re.compile(r"\b(?:crontab\s+-r|systemctl\s+(?:disable|mask)\b)", re.I),
           "persist-off", "拆掉系统级定时/服务"),
    # ── 凭据与敏感面 ───────────────────────────────────────────
    Danger(re.compile(r"(?:/etc/shadow\b|\bSAM\b\s*$|\bNTDS\.dit\b|\.aws/credentials)", re.I),
           "creds", "读凭据库：越出渗透范围，属主机接管"),
    # ── 把远程内容直接执行 ─────────────────────────────────────
    Danger(re.compile(r"\b(?:curl|wget)\b[^|]*\|\s*(?:sudo\s+)?(?:ba)?sh\b", re.I),
           "curl-pipe-sh", "远程脚本直接执行：内容不受控"),
    Danger(re.compile(r"\b(?:iwr|invoke-webrequest|curl)\b[^|]*\|\s*(?:iex|invoke-expression)\b", re.I),
           "iwr-pipe-iex", "远程脚本直接执行（PowerShell）"),
    Danger(re.compile(r"\bpowershell(?:\.exe)?\b[^|]*-e(?:nc|ncodedcommand)?\b", re.I),
           "ps-encoded", "PowerShell 编码命令：内容不可读"),
    Danger(re.compile(r"\bbase64\s+(?:-d|--decode)\b[^|]*\|\s*(?:ba)?sh\b", re.I),
           "b64-pipe-sh", "解码即执行"),
    Danger(re.compile(r":\(\)\s*\{.*\}\s*;\s*:", re.S), "fork-bomb", "fork 炸弹"),
)


def check_command(command: str) -> Danger | None:
    """命中就返回那一条（带名字与原因），否则 None。

    只做**静态**判断：不做 shell 解析、不追变量展开。所以它的定位是"挡明显的手滑
    与抄来的危险命令"，不是"证明命令安全"——后者的代价是骗自己。
    """
    text = command.strip()
    if not text:
        return None
    for item in DANGEROUS:
        if item.pattern.search(text):
            return item
    return None


__all__ = ["DANGEROUS", "Danger", "check_command"]
