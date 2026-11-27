"""B86 · 具体路径 → 归一模板（观察面必须归一，否则 1000 个 id 就是 1000 条"面"）。

归一规则只有三条，**宁可少归一**：把 ``users`` 也归成 ``{p}``，
会让两条本来不同的面撞成同一个 ref，而 ref 是胶囊与报告的锚点。

1. 全数字段（``123``、``2024``）；
2. UUID / 长十六进制段（≥8 位且只含 ``0-9a-f-``）；
3. 含 ``@`` 的邮箱形段。

参数名按出现顺序给 ``p1..pn``——与 ``hos_ares_contract.parser.normalize_template``
的 ``{p}`` 归一配套：模板里保留真名，结构身份仍由归一模板算。

ponytail: 三条启发式的天花板是"语义 id"（``user-<name>``、``order_2024_x``）不归一，
它们会各占一条面。真实目标上出现面数爆炸时，再按"同前缀段数 ≥N 且值各不相同"补规则。
"""

from __future__ import annotations

SEP = chr(0x2F)

#: 段被视为十六进制 id 的最短长度。短于它的（``v1``、``api``）一律当字面量。
_HEX_MIN = 8

_HEX_CHARS = frozenset("0123456789abcdef-")


def _split_ext(segment: str) -> tuple[str, str]:
    """``9825.htm`` → ``("9825", ".htm")``；没有短后缀就整段返回。

    为什么要拆：CMS 的文章页形如 ``/info/12/9825.htm``，不拆的话每个文章号
    都是"另一条面"——一次首页抓取就能攒出几十条同模板实例，
    覆盖率分母当场失真。
    """
    head, dot, tail = segment.rpartition(".")
    if dot and 1 <= len(tail) <= 5 and tail.isalnum() and head:
        return head, dot + tail
    return segment, ""


def _is_dynamic(segment: str) -> bool:
    if not segment:
        return False
    if segment.isdigit():
        return True
    if "@" in segment:
        return True
    low = segment.lower()
    return len(low) >= _HEX_MIN and all(c in _HEX_CHARS for c in low)


def templatize(path: str) -> tuple[str, tuple[str, ...]]:
    """``/api/users/123`` → ``(/api/users/{p1}, ("p1",))``。

    参数名按**出现顺序**编号，不带原始段（原始 id 是数据，不是结构）。
    结构身份随后由 ``normalize_template`` 统一成 ``{p}``，编号不外泄。
    """
    out: list[str] = []
    names: list[str] = []
    for segment in path.split(SEP):
        stem, ext = _split_ext(segment)
        if _is_dynamic(stem):
            name = f"p{len(names) + 1}"
            names.append(name)
            out.append("{" + name + "}" + ext)
        else:
            out.append(segment)
    template = SEP.join(out)
    if len(template) > 1 and template.endswith(SEP):
        template = template.rstrip(SEP) or SEP
    return template, tuple(names)


__all__ = ["templatize", "templatize_many"]


#: 同类多值归一的阈值：同一位置出现这么多种不同值，就认为它是 id。
#:
#: **3 而不是 2**：`/api/v1/x` 与 `/api/v2/x` 只有两个值，它们是**真不同的面**
#: （版本端点）。三条以上同位置异值，才更像"同一个面被 id 撑开"。
_SAME_SLOT_MIN = 3


def _is_slot_only_difference(group: list[list[tuple[str, str]]], slot: int) -> bool:
    """这个 slot 是不是**唯一**的区别位置。

    **这条判据是 B609 补的，起因是一个真误伤**：

    ```
    /api/echo   /api/fetch   /api/login   /api/me   /api/ping  ...
    ```

    这些是**不同的端点**，但它们在 slot 1 上值全不同 —— 只按"同位置异值≥3"
    判，会把 24 条不同的面归成 3 条，**分母被压塌**（比虚增更糟：
    真实的面被合掉了，报告里会少检测项）。

    真正的语义 id 有更强的结构：**共用固定前缀、只在某一中间段不同、
    后续段固定**（`/api/user-A/profile` vs `/api/user-B/profile`）。
    所以这里要求：

    1. 该 slot 之后**至少还有一段**（末段是资源名时，异值就是不同端点）；
    2. 该 slot 之后的所有段**组内完全一致**；
    3. 该 slot 之前的所有段也组内一致。
    """
    n = len(group[0])
    if slot == n - 1:
        return False        # 末段异值 = 不同端点，不是 id
    for other in range(n):
        if other == slot:
            continue
        values = {segs[other] for segs in group}
        if len(values) != 1:
            return False    # 别处也在变 —— 这不是"一个 id 位"，是不同面
    return True


def templatize_many(paths: list[str]) -> dict[str, tuple[str, tuple[str, ...]]]:
    """批量归一：**同位置异值 ≥3 种的段，按参数归一**。

    这是 ``templatize`` 的天花板补齐（见模块 docstring 的 ``ponytail:`` 注）。
    单条路径看不出"同类多值"，必须成组看：

    ```
    /api/user-A/profile      ┐
    /api/user-B/profile        ├─ 位置 2 三个异值 → 归成 /api/{p1}/profile
    /api/user-C/profile      ┘
    ```

    而 ``templatize`` 对这三条会给三个不同的模板，于是 crawl 把同一条语义端点
    当成三条面各抓一遍——**覆盖率分母被虚增**。

    ## 与 ``templatize`` 的关系

    - ``templatize`` 只认**结构性**动态段（数字 / hex / 邮箱），**不动**语义 id；
    - 本函数在成组上下文里补上语义 id；
    - 两条规则都守着同一件事：**宁可少归一**。所以阈值是 3，
      且只归一"每一条路径在该位置都有值、且值全不相同"的段。

    ## 参数名

    先跑 ``templatize``（结构性动态段已经是 ``p1..pn``），再给补充归一的段
    续号，**不重排**——重排会让已有模板的 ref 变化，胶囊与报告会对不上。

    返回：``{原始路径: (模板, 参数名tuple)}``。没被归一的位置保持字面量。
    """
    if not paths:
        return {}

    # 每条的段列（拆扩展名，与 templatize 一致）
    split: list[list[tuple[str, str]]] = []
    for p in paths:
        split.append([_split_ext(seg) for seg in p.split(SEP)])

    # 只看长度相同的组：段数不同的路径不可能同构
    # （`/api/x/a` 与 `/api/y/b/c` 是两条不同的面，别硬归）。
    by_len: dict[int, list[int]] = {}
    for idx, segs in enumerate(split):
        by_len.setdefault(len(segs), []).append(idx)

    # 位置 → 是否该归一
    promote: dict[tuple[int, int], bool] = {}
    for _length, members in by_len.items():
        if len(members) < _SAME_SLOT_MIN:
            continue
        group = [split[i] for i in members]
        slots = len(group[0])
        for slot in range(slots):
            values = {segs[slot][0] for segs in group}
            # 结构化动态段已经是参数了（`templatize` 会认），这里只管剩下的
            literal = {v for v in values if not _is_dynamic(v)}
            if len(values) < _SAME_SLOT_MIN or len(literal) != len(values):
                continue
            # **必须是"唯一区别位"**（B609）：否则 `/api/echo` `/api/fetch`
            # `/api/login` 这些不同端点会被归成同一条面，分母被压塌。
            if not _is_slot_only_difference(group, slot):
                continue
            for i in members:
                promote[(i, slot)] = True

    out: dict[str, tuple[str, tuple[str, ...]]] = {}
    for idx, p in enumerate(paths):
        base_template, base_names = templatize(p)
        if not promote:
            out[p] = (base_template, base_names)
            continue
        # 已由 templatize 认掉的段数（用于给补充归一续号）
        used = len(base_names)
        rebuilt: list[str] = []
        extra: list[str] = []
        for slot, (stem, ext) in enumerate(split[idx]):
            if promote.get((idx, slot)):
                used += 1
                name = f"p{used}"
                extra.append(name)
                rebuilt.append("{" + name + "}" + ext)
            elif _is_dynamic(stem):
                # **必须保留 templatize 已经给出的参数名**（B607 踩过）：
                # `/api/1/A/x` 里 `1` 是结构 id、`A` 是语义 id，
                # 两边都归一后若这里直接输出字面量 `1`，
                # 模板会变成 `/api/1/{p2}/x` —— 结构 id 丢了参数化，
                # 而 `p1` 还挂在 `base_names` 里，编号对不上。
                # 参数名按出现顺序重新数：这是本条路径里的第几个动态段。
                nth = sum(1 for s2 in range(slot) if _is_dynamic(split[idx][s2][0]))
                rebuilt.append("{" + f"p{nth + 1}" + "}" + ext)
            else:
                rebuilt.append(stem + ext)
        template = SEP.join(rebuilt)
        if len(template) > 1 and template.endswith(SEP):
            template = template.rstrip(SEP) or SEP
        out[p] = (template, base_names + tuple(extra))
    return out
