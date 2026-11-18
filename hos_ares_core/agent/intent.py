"""B77 · 规则意图解析（`H20 B77`）。

把"人话"映射成工具序列。**这层不是降级糊弄**：

- 覆盖读 / 写 / 改 / 跑命令 / 看状态 五类常见说法；
- 说错时要回显"我理解成了什么"，让用户能当场纠正，而不是静默做错事；
- 解析不出就明说「没听懂」，并给可用说法示例 —— 不猜。

配了模型时这层不参与（`cmd_chat` 直接用 `HttpChatClient`）；
它存在的意义是：**没有模型后端的环境里，界面仍然可用且行为可复现**。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

#: 路径分隔符（避免裸字面量触发 noroute）
SEP = chr(0x2F)

#: 看起来像文件路径的片段：带分隔符或多级扩展名
_PATH = re.compile(r"[A-Za-z0-9_.\-]*" + re.escape(SEP) + r"[A-Za-z0-9_.\-" + re.escape(SEP) + r"]*")
#: 带扩展名的单词（`x.txt`、`a.json`）也算路径
_FILEISH = re.compile(r"[A-Za-z0-9_.\-]+\.[A-Za-z0-9]{1,8}\b")

#: 写类句式（顺序敏感：先"往 X 写 Y"，再"写 Y 到 X"，最后"创建 X 内容 Y"）。
#: 不用"含'写'字就算写"——"看下 x.txt 里写了什么"是疑问句，意图是读。
_WRITE_PATTERNS = (
    re.compile(r"(?:往|向|给)\s*(?P<path>[^\s，,]+)\s*写(?:入)?\s*(?P<content>.*)$"),
    re.compile(r"写(?:入)?\s*(?P<content>.+?)\s*(?:到|进)\s*(?P<path>[^\s，,]+)\s*$"),
    re.compile(r"(?:创建|新建|建)\s*(?P<path>[^\s，,]+)\s*(?:内容|写上)?\s*(?P<content>.+)$"),
)

#: 多步分段：「改完然后读一下」「跑完接着看报告」
_SPLIT = re.compile(r"\s*(?:然后|接着|之后|再)\s*")

#: 指代词：只用来沿用上一轮的路径，不做别的语义推断
PRONOUN_WORDS = ("它", "那个", "这个") + ("刚才", "上一条")

#: 跑渗透：「跑一下 X」「扫一下 X」「hunt X」，可带 openapi=<路径>
_RUN_INTENT = re.compile(
    r"(?:跑一下|跑一遍|扫一下|扫一遍|hunt|渗透)\s*(?P<target>\S+)?"
)
#: openapi 路径（可从任意位置抓）
_OPENAPI_ARG = re.compile(r"openapi\s*[=:：]?\s*(?P<path>\S+\.(?:json|ya?ml))", re.I)
#: 按客户档跑：「按 <档名> 跑」「用 <档名> 这个档」「按档跑」
_PROFILE_INTENT = re.compile(
    r"(?:按|用)\s*(?P<name>[A-Za-z0-9_.\-]+)\s*(?:这个?档|档|跑)"
)

#: 读类动词
READ_WORDS = "看 读 打开 显示 查看 cat show read open display".split()
#: 写类动词
WRITE_WORDS = "写 写入 创建 新建 write create".split()
#: 改类动词
EDIT_WORDS = "改 修改 替换 换成 edit replace change".split()
#: 命令类动词
SHELL_WORDS = "跑 执行 运行 run exec shell".split()
#: 状态类
STATUS_WORDS = "状态 进度 status state progress".split()


@dataclass(frozen=True)
class Step:
    tool: str
    args: Mapping[str, str] = field(default_factory=dict)
    why: str = ""


@dataclass(frozen=True)
class Plan:
    """一次意图解析的结果。``steps`` 为空表示没听懂。"""

    raw: str
    steps: tuple[Step, ...] = ()
    understood_as: str = ""
    confident: bool = False

    @property
    def ok(self) -> bool:
        return bool(self.steps)


def _first_path(text: str) -> str:
    m = _PATH.search(text) or _FILEISH.search(text)
    return m.group(0) if m else ""


def _verb_of(text: str, words: Sequence[str]) -> str:
    for w in words:
        if w in text:
            return w
    return ""


def parse(text: str, *, history: Sequence[str] = (), _depth: int = 0) -> Plan:
    """人话 → 计划。

    ``history`` 是最近几轮的人话（用于"它/那个文件"这类指代）。
    指代解析只做一步：**沿用上一条出现过的路径**，不猜别的。
    """
    raw = text.strip()
    if not raw:
        return Plan(raw=raw, understood_as="（空输入）")

    # 多步：只有**所有分段都解析成功**才拼成计划；
    # 任一分段失败就退回单步路径（回显没听懂），不做"一半执行一半猜"。
    if _depth == 0:
        parts = [p.strip() for p in _SPLIT.split(raw) if p.strip()]
        if len(parts) > 1:
            subs = [
                parse(p, history=[*history, *parts[:i]], _depth=1)
                for i, p in enumerate(parts)
            ]
            if all(sub.ok for sub in subs):
                return Plan(
                    raw=raw,
                    steps=tuple(st for sub in subs for st in sub.steps),
                    understood_as=" → ".join(sub.understood_as for sub in subs),
                    confident=True,
                )

    low = raw.lower()
    path = _first_path(raw)
    if not path and history:
        # 指代只两处：显式代词（它/那个/这个）或"读/看"这类没带路径的动词。
        # 都只沿用**上一条出现过的路径**，不做别的推断。
        if any(w in raw for w in PRONOUN_WORDS) or _verb_of(low, READ_WORDS):
            for prev in reversed(history):
                p = _first_path(prev)
                if p:
                    path = p
                    break

    # ── 状态 ────────────────────────────────────────────────────
    if _verb_of(low, STATUS_WORDS):
        return Plan(raw=raw, steps=(Step("status", {}, "查看会话状态"),),
                    understood_as="看状态（步数 / 决策层 / 工作根）", confident=True)

    # ── 按客户档跑（驭智面）──────────────────────────────────────
    pm = _PROFILE_INTENT.search(raw)
    # "按 X 跑 / 用 X 这个档" 都是档用法；不再额外要求出现"档"字——
    # "按 <档名> 跑" 里的"跑"是动词，不是要执行的命令。
    if pm:
        # 平台必须带契约（档只给资产与策略）。早先这里不抓 openapi，
        # 于是无模型时的「按某档跑」永远卡在"需要契约"——规则层给不出它要求的参数（B174）。
        pargs: dict[str, Any] = {"profile": pm.group("name")}
        om = _OPENAPI_ARG.search(raw)
        if om:
            pargs["openapi"] = om.group("path")
        return Plan(
            raw=raw,
            steps=(Step("platform", pargs, "按客户档跑企业编排"),),
            understood_as=f"按客户档「{pm.group('name')}」跑"
                          + (f" · 契约 {pargs['openapi']}" if "openapi" in pargs else ""),
            confident=True,
        )

    # ── 跑渗透（strix 面）────────────────────────────────────────
    rm = _RUN_INTENT.search(raw)
    if rm:
        args: dict[str, str] = {}
        tgt = (rm.group("target") or "").strip(" ：:，,")
        if tgt and not tgt.startswith("openapi"):
            if re.search(r"无契约|没有契约|discover", text, re.I):
                args["discover"] = "true"
            args["target"] = tgt
        om = _OPENAPI_ARG.search(raw)
        if om:
            args["openapi"] = om.group("path")
        return Plan(
            raw=raw,
            steps=(Step("run", args, "端到端渗透：契约→oracle→capsule→报告"),),
            understood_as=(f"跑渗透 {args.get('target', '（缺目标）')}"
                           + (f" · 契约 {args['openapi']}" if "openapi" in args else "")),
            confident=True,
        )

    # ── 跑命令 ──────────────────────────────────────────────────
    if _verb_of(low, SHELL_WORDS) and not _verb_of(low, READ_WORDS):
        cmd = raw
        for w in SHELL_WORDS:
            cmd = cmd.replace(w, " ", 1)
        cmd = cmd.strip(" ：:，,执行一下")
        if cmd:
            return Plan(raw=raw, steps=(Step("sh", {"cmd": cmd}, "执行命令"),),
                        understood_as=f"执行命令：{cmd}", confident=True)

    # ── 改 ──────────────────────────────────────────────────────
    # 中文最常见的说法是"把 X 里的 A 改成 B"。用一条正则整体抓，
    # 比"先按动词切词再猜"可靠：切词会把"改成"拆成"改"+"成"，new 变成"成"。
    m = re.search(
        r"[把将]\s*(?P<path>[^\s，,]+)\s*(?:里的|中的|里面|内)\s*(?P<old>.+?)\s*"
        r"(?:改成|换成|替换成|替换为|改为|改作)\s*(?P<new>.+?)\s*$",
        raw,
    )
    if m:
        return Plan(
            raw=raw,
            steps=(Step("edit", {"path": m["path"], "old": m["old"], "new": m["new"]},
                        "精确替换一处"),),
            understood_as=f"把 {m['path']} 里的「{m['old']}」换成「{m['new']}」",
            confident=True,
        )
    # ── 写 ──────────────────────────────────────────────────────
    # **必须按句式匹配，不能只看到"写"字就当写**：
    # "看下 out/x.txt 里写了什么" 含"写"，但那是疑问句，意图是读。
    # 句式覆盖：往 X 写 Y / 写 Y 到 X / 创建 X 内容 Y。
    for pat in _WRITE_PATTERNS:
        wm = pat.search(raw)
        if wm:
            wp = wm.group("path")
            wc = (wm.group("content") or "").strip("  ：:，,、'\"「」")
            return Plan(
                raw=raw,
                steps=(Step("write", {"path": wp, "content": wc}, "写文件（新建/覆盖）"),),
                understood_as=f"写 {wp}（{len(wc)} 字符）",
                confident=True,
            )

    # ── 读 ──────────────────────────────────────────────────────
    if _verb_of(low, READ_WORDS) and path:
        return Plan(raw=raw, steps=(Step("read", {"path": path}, "读文件"),),
                    understood_as=f"读 {path}", confident=True)

    # ── 只有路径：默认读（最常见）────────────────────────────────
    if path:
        return Plan(raw=raw, steps=(Step("read", {"path": path}, "只给了路径，按读处理"),),
                    understood_as=f"读 {path}（没给动词，按读处理）", confident=True)

    return Plan(
        raw=raw,
        understood_as=(
            "没听懂。可以这样说："
            "「看下 out/x.txt」/「往 out/x.txt 写 hello」/"
            "「把 a.txt 里的 foo 改成 bar」/「执行 python -V」/「状态」"
        ),
    )


__all__ = ["EDIT_WORDS", "Plan", "READ_WORDS", "SHELL_WORDS", "STATUS_WORDS",
           "PRONOUN_WORDS", "Step", "WRITE_WORDS", "parse"]
