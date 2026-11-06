# H27 · strix 模块收割与无契约发现

> 结论先说：**参考实现已经在树里**（`vendor/strix` @ `6b7ae2e`，Apache-2.0），
> 不需要"再研究一遍 strix 是什么"。要做的只有一件事——**逐模块判"抄不抄、怎么抄"**，
> 然后把最堵的那一段（目标没有契约）先打通。
>
> 本卷记两件事：**收割矩阵**（§2）与 **B86 / B87 / B89 已落地**（§3）；
> **其余四项核对后全部结案**（§4）——六个「要抄」里有三个是我们本来就有的，复盘见 §4.1。
> H07 的 25 张卡是 v1.0 冻结账本，本卷的 B86+ **不回头改 H07**——
> 改了就要重算四波合计，而那份合计已经当验收依据用过。新批次记在这里，一并记进 H01 的回填。

---

## 0. 一句话

抄 strix 的**执行面**与**观察面**，不抄它的**判定**。
B86 之后，"目标没有 OpenAPI"不再等于"工具停在门口"：
`veripen discover` 从首页攒面，本地 WAF 靶场实测 1 页 → 47 条面，2 轮 → 52 条。

---

## 1. 资产与四条纪律

| 项 | 值 |
|---|---|
| 位置 | `vendor/strix/`（`noroute` / `nodata_stub` 都扫不到它：`vendor/` 不在扫描范围） |
| 版本 | commit `6b7ae2ebe4d47037a927aade23265ff4e4f27e84`，tarball sha256 记在 `.vendor-meta.json` |
| 许可 | Apache-2.0 → **抄代码必须在文件头注明来源与 commit**，不是"参考了一下" |
| 规模 | `strix/**` 共 ~110 个 `.py`，21 个工具模块，9 类 skill |

**四条纪律**（这四条决定了下面矩阵里为什么有一半是"不抄"）：

1. **只抄执行面与观察面**：容器、工具面、覆盖账本、报告格式——这些是"怎么动手"。
2. **判定不抄**：strix 让模型给结论（`report/writer.py` 的 findings 由 agent 写），
   我们 `veripen_oracles/` 是纯函数 + 三条件 + control，`H05`/`H01 V1` 的领先项就在这里。
3. **地址不抄**：strix 把 target URL 写进任务与容器环境；我们 `RouteRef` 是不透明句柄，
   地址只由 resolver 产出（`H01 V3` 是我们唯一一条"开源也没做对"的）。
4. **依赖不抄**：Caido（必须常驻的代理进程）、PostHog/Scarf（外发遥测）
   都不进产品。抄形态，不抄它们带来的运行期负担。

---

## 2. 收割矩阵（四类，逐模块）

图例：**直接抄** = 文件级移植，改 import 即可；**改造抄** = 借结构、换后端；
**只抄设计** = 借接口形状与纪律，代码自己写。

### 2.1 直接抄（低耦合、纯工具，收益立刻可见）

| strix 模块 | 行 | 我们的洞 | 批次 |
|---|---:|---|---|
| `tools/output_store.py` | 166 | agent 历史没有上界 | **已存在，不抄**（见下） |
| `config/tool_call_limits.py` | 46 | 单轮工具调用风暴 | **已存在，不抄**（见下） |
| `utils/api_spec.py` | 312 | 只认 OpenAPI 3：给一份 swagger 2.0 或 Postman collection，`parse_openapi` 直接拒 | **B89 已落** |

`output_store.py` 的分工值得原样保留：**head + tail 切片 + 溢出到文件路径**，
让 agent 自己用文件工具读剩下的。它不强塞摘要、不做语义压缩——
压缩算法留在 `compression.py`，这两件事不打架。

`api_spec.py` 只抄**两件事**：`classify_spec`（openapi / swagger / postman）
与 `spec_base_urls`（声明 host 提取，供 scope 授权）。
operations 的解析仍走我们 `veripen_contract/parser.py`——
它的三条硬边界（纯函数 / 不出地址 / 零猜）不能因为抄而松掉。

**B88 结案：不需要抄。** 核对现有代码后推翻立项判断：

| strix | 我们已有的 | 结论 |
|---|---|---|
| `output_store.py`（head/tail 字节切 + 溢出到路径） | `veripen_core/tools/registry.py#bound_and_store`：头尾**整行**切 + 超长行硬切 + `dropped_lines/bytes` + 全文落盘 `persisted_ref` | 我们更细，抄回去是降级 |
| `tool_call_limits.py`（一次响应最多 K 个调用） | `TOOL_CALLS_PER_STEP = 1`（`veripen_core/tools/registry.py`）：一步一个，超出直接拒 | 我们更严，抄了等于放宽 |

这条记录本身是收割矩阵的用法：**先核对自己有没有，再决定抄不抄**。
"不要什么都自己做"的反面同样成立——不要重复做已经做完的事。

### 2.2 改造抄（借结构、换后端）

| strix 模块 | 行 | 我们的洞 | 改什么 | 批次 |
|---|---:|---|---|---|
| `tools/proxy/tools.py` + `caido_api.py` | 701+ | 无契约时面从哪来 | 观察点从**代理流量**换成**响应体**；不引 Caido | **B86 已落** |
| `report/coverage.py` + `tools/coverage/tools.py` | 443 / 535 | V4：会扫不会算"漏了什么"（真实目标召回 0.33） | 双源 provenance + gaps 表；接到我们的四态 outcome | **B87 已落** |
| `report/dedupe.py` + `report/sarif.py` | 418 / 1185 | 同一漏洞在多条路重复计数；客户/CI 要 SARIF 2.1 | 去重键换成 `structural_key`；SARIF 从 capsule 生成 | B90 |
| `runtime/docker_client.py` + `session_manager.py` | 320 / 477 | E1 真隔离缺后端（本机无 docker，`select_executor` 只能给 job-object） | 接到 `veripen_core/sandbox/runner.py` 的协议上 | B91 |
| `llm/compaction.py` | 401 | 压缩**触发策略**（我们 `compression.py` 有算法，没有"什么时候压"） | 只抄阈值与触发点 | B92 |

**B87 值得单独说**：strix 的 `coverage.py` 里有一条纪律，我们目前没有——

> `agent_reported`（agent 说"我查过") 与 `machine_observed`（运行时真的记录了什么）
> **必须分开存**，而且机器观测**只能反驳、不能确认**：
> agent 带了 `sql_injection` 技能却一行覆盖记录都没写 → 进 `gaps`；
> 跑飞了的 run 一律 `complete: false`，账本再整齐也不行。

这正好是我们 V4 的病灶：现在只有"跑了哪些 oracle"，没有"哪些面本该跑却没跑"。

### 2.3 只抄设计

| strix 模块 | 借什么 | 为什么不抄代码 |
|---|---|---|
| `tools/agents_graph/tools.py`（855） | `spawn / send / wait / finish` 的子代理接口形状 | 我们 `agents/orchestrator.py` 已有编排与黑板冲突消解，接口换名即可，不必重写 |
| `skills/*`（9 类）+ `tools/load_skill` | **按需 inline 加载**：不改常驻提示、单次 ≤5 条、正文作为工具结果进入对话 | 我们 `playbooks/` 是自己的成功率语义资产；借的是"加载方式"，不是内容 |
| `tools/mcp/*` | MCP client 的会话/命名/失败面 | 我们 `plugins/` 已有装→用→升级→卸；只在需要外接第三方工具时接 |

`load_skill` 那条设计值得记住的一句话是：
**"永久分配用 `create_agent(skills=[...])`，临时查用 `load_skill`"**——
两种加载各有归属，不混在一条路径上。我们 playbook 现在只有"常驻"，没有"临时查"。

### 2.4 不抄

| 不抄 | 理由 |
|---|---|
| 模型直接给 verdict | `H01 V1` 的领先项，抄了就退化成"不可 replay 的结论" |
| 地址写进任务/容器环境/轨迹 | `H01 V3`；这是唯一一条"开源也没做对"的红线 |
| Caido 依赖 | 多一个必须常驻的代理进程；我们的观察点更靠下（响应体），不需要中间人 |
| PostHog / Scarf 遥测 | 产品不出网这件事不因为"匿名"就成立 |
| Claude/OpenAI 特定 tool-call 形状 | 我们 `veripen_core/agent/client.py` 是网关中立形态，绑定单一供应商等于自断模型换代 |

---

## 3. 已落地

### 3.1 为什么是"轮次"而不是"爬虫"

爬链接就要发请求，发请求就必须持 `RouteBinding`——而绑定只能由 resolver 产出（N2）。
所以一次抓到的东西**不能**直接拿去抓下一层。改成轮次：

```
第 n 轮：候选模板 → resolver → 绑定 → 抓 → 抽入口 → 候选模板（下一轮）
```

代价是"深度 = 轮数"；收益是**地址纪律在发现路径上不被破例**。
爬得再深，也只是多几轮，不是多一条旁路。

### 3.2 三个新模块（各管一层，互不越界）

| 模块 | 只做什么 |
|---|---|
| `veripen_core/discovery/links.py` | HTML 文本 → `Sighting`（method + 相对路径 + 参数名），**纯函数** |
| `veripen_core/discovery/templatize.py` | 具体路径 → 模板 + 参数名，**纯函数** |
| `veripen_core/discovery/crawl.py` | 已绑定的面 → `ObservedRecord` + 下一轮入口（发请求由注入的 fetcher 做） |

配套改动：

| 文件 | 改了什么 |
|---|---|
| `veripen_core/routes/sources/observed.py` | 加 `CrawlSource`：形态同 `ObservedSource`，只换 provenance（0.70 vs 1.00） |
| `veripen_core/routes/sources/__init__.py` | `default_sources` 里的 `crawl` 从空适配器换成真实现 |
| `veripen_core/tools/http.py` | 加 `send_body`：与 `send` 共用同一条闸门链，多返回响应体 |
| `veripen_cli/cmd_discover.py` | 新子命令 `veripen discover`（不产判定） |
| `veripen_cli/__main__.py` | 注册 `discover` |

**为什么 `send_body` 必须共用闸门**：读 HTML 也是一次出网。
如果为了拿 body 另开一条"只读不审"的近路，审计就会在**发现阶段**出现盲区——
而那一段恰恰最容易被绕过。

### 3.3 实测（本地 WAF 靶场，`www.sias.edu.cn`）

```
$ veripen discover --target https://www.sias.edu.cn --max-rounds 2 --max-pages 6 --rate 2
第 1 轮：抓 1 页 / 新面 47 条
第 2 轮：抓 6 页 / 新面 5 条（本轮触到页数/记录上限）
目标：www.sias.edu.cn
发现：52 条面 / 抓 7 页 / 置信 0.70（crawl，未验证）
  · /info/{p1}/{p2}.htm      ← 20+ 条文章链接归成一条面
  · /xxgk/xxjj.htm
  · /jxky/kygk.htm
  …
```

两次修正来自实测，不是设计推演：

| 现象 | 原因 | 修法 |
|---|---|---|
| 44 条面里 20 条是同一模板 | 文章号形如 `9825.htm`，带后缀不算"全数字" | 先剥短后缀再判动态段，模板保留 `.htm` |
| 出现 `/../../gjjl/x.htm` | 相对链接没相对**当前页**解析 | `extract(base_path=...)` 消 `.`/`..`；根以上的 `..` 丢弃 |

### 3.4 判据（每条都能红）

| 判据 | 怎么验 | 注入什么会红 |
|---|---|---|
| 只收本机入口 | `test_extract_keeps_local_and_drops_external` | 收下 `https://…` 外链 → 跨资产越界 |
| 归一不多归 | `test_templatize_keeps_version_and_word_segments` | 把 `users` 归成 `{p}` → 两条面撞成一个 ref |
| 轮次不重复抓 | `test_crawl_does_not_refetch_a_seen_page` | 去掉 `_seen` → 第二轮又抓首页，预算翻倍 |
| 看见 ≠ 打过 | `test_crawl_source_confidence_is_lower_than_observed` | 让 crawl 也用 1.00 → 覆盖率虚高 |
| 无面不静默成功 | `main` 返回 1 | 返回 0 → "什么都没发现"被当成"干净" |

### 3.5 天花板（明写，不藏）

- **语义 id 不归一**：`user-alice`、`order_2024_x` 仍各占一条面。
  真实目标上出现面数爆炸时，补"同前缀段数 ≥N 且值各不相同"的规则。
- **不执行 JS**：SPA 的客户端路由看不见。要看得见就得上 `render/browser`，
  那是另一批（B46 已有渲染体，但没接进发现）。
- **不跟外链**：跨域 API 一律丢。这是纪律，不是缺陷。

---

### 3.6 B87 · 覆盖账本（回答"漏了什么"）

抄的是 strix `report/coverage.py` 的**那条纪律**，不是它的数据结构：

> `agent_reported` 与 `machine_observed` 分开存；
> 机器观测**只能反驳、不能确认**——喊过的必须被机器印证。

我们这边"面"是 `RouteBinding`（不透明 ref），"收口"是四态 outcome
（reported / no_issue_found / blocked / undetermined / error）。

| 文件 | 作用 |
|---|---|
| `veripen_core/coverage.py` | `build_coverage`（纯函数）+ `write_coverage`（临时文件 + 替换） |
| `veripen_cli/cmd_run.py` | 组装账本 → 落 `coverage.json` → 控制台摘要 → **报告第 4 节点名缺口** |
| `veripen_cli/cmd_hunt.py` | 同口径接账本；`hunt.json` 内嵌 summary；完整度只认「agent 自己说收尾」 |
| `veripen/report/render.py` | `_render_coverage` 增「覆盖缺口」子节：只统计不点名的覆盖节，判读者看不出哪条面没测 |

六类缺口，**每类都指名道姓**（不是"覆盖不足"这种形容词）：

| kind | severity | 含义 |
|---|---|---|
| `face_without_oracle` | critical | 这条面没有任何 oracle 能测——不是"测过没问题" |
| `oracle_not_run` | critical | 标签本可由某 oracle 判定，本轮没执行 |
| `intentional_exclusion` | info | 显式排除（写类/压力类）：没测，但留名 |
| `oracle_blocked` / `_undetermined` / `_error` | warn | 跑不通 ≠ 干净 |
| `low_confidence_face` | info | 面来自 crawl/probe（<0.85），是推断或看见过 |
| `claim_without_observation` | critical | agent 声称测过，机器没有执行记录 |

实测（本地夹具 + 一条 `object_read` 面）：

```
  idor_cross_account       BLOCKED        baseline_abnormal
结论：VERIFIED 0 / 共 1 项
覆盖：1/1 面 / 适用 1 对 / 执行 1 / 缺口 1 → coverage.json
  注意：1 项缺口使得本账本**不能**读作完整覆盖。
summary: {"faces":1,"faces_covered":1,"applicable_pairs":1,"executed":1,
          "outcomes":{"blocked":1},"gaps":1}
complete: False      gaps: [('oracle_blocked','warn')]
```

这一行就是 B87 的全部意义：**同一个"0 发现"，账本会告诉你它是"没测"还是"测过"**。

判据：`tests/unit/test_coverage.py` 11 条——面没 oracle、该跑没跑、BLOCKED≠干净、
排除要留名、声称要被印证、猜出来的面要标、干净轮次必须 complete。

---

### 3.7 B89 · 契约格式识别（swagger 2.0 / Postman）

`--openapi` 这个名字骗人：客户给的东西经常是 **swagger 2.0**（2017 年前的
工具链都吐这个）或 **Postman collection**。直接喂给 `parse_openapi`，
报错是"paths 不是对象"——读的人会以为文件坏了，其实是格式没认出来。

| 文件 | 作用 |
|---|---|
| `veripen_contract/spec_detect.py` | `classify` / `spec_base_urls` / `swagger_to_openapi` / `as_openapi_text` |
| `veripen_cli/cmd_run.py`、`cmd_hunt.py` | 读文件后先归一；Postman/认不出 → 当场报 VP-1010 并退 2 |

**一个必须做的取舍**：swagger 的 `basePath` 折进路径，而不是只放进 `servers`。
因为我们的绑定层只给 host 补前缀（`SymbolSource` + `--target`），
路径前缀不会自动加回来——留在 `servers` 里，整批请求会静默打到 404 上。
这是"我们的流水线怎么走"决定的事，不是标准怎么说的事。

实测（本地夹具 + 一份 2017 风格契约）：

```
$ veripen run --target http://127.0.0.1:PORT --openapi swagger.json --confirm-structural
路由：1 条绑定 / 可跑 oracle 1 个
  idor_cross_account       BLOCKED        baseline_abnormal
覆盖：1/1 面 / 适用 1 对 / 执行 1 / 缺口 1 → coverage.json
faces: [('http://127.0.0.1:PORT/v1/users/{id}', 'contract')]
```

`/v1` 在模板里——这就是上面那条取舍落地后的样子。

判据：`tests/unit/test_spec_detect.py` 12 条——识别三格式、body→requestBody、
响应 schema→content、Postman 明确拒绝、归一后真的能出 operation。

---

## 4. 收割结论（原排期 B90–B92 全部结案）

立项时按 strix 的目录结构排了六批"要抄"。逐条核对我们的代码后，**只有三批是真缺口**：

| 批 | 立项判断（要抄） | 核对结论 | 证据 |
|---|---|---|---|
| B86 | 代理观察 → 面清单 | **真缺口**，已实现（观察点换到响应体） | `veripen_core/discovery/` |
| B87 | 覆盖账本双源 + gaps | **真缺口**，已实现 | `veripen_core/coverage.py` |
| B89 | swagger/Postman 识别 | **真缺口**，已实现 | `veripen_contract/spec_detect.py` |
| B88 | `output_store` / `tool_call_limits` | **已有，且更强** | `tools/registry.py#bound_and_store`（整行切 + 落盘 + dropped 计数）；`TOOL_CALLS_PER_STEP = 1` |
| B90 | `dedupe` / `sarif` | **SARIF 已有**（`render_sarif` + 测试）；去重经核对无来源：resolver 已按 `structural_key` 合并同一面，一条面一个 oracle 一条 finding | `veripen/report/render.py:301`、`tests/unit/test_report.py` |
| B91 | docker 后端 | **已有**（加固 argv 的 `DockerExecutor`），本机无运行时 → 与 A17 同样"未验" | `veripen_core/sandbox/runner.py:60` |
| B92 | 压缩触发 | **已有生产接线**（`briefing` → `compress`） | `veripen_core/briefing.py:96` |

唯一剩下的是 **playbook 的"临时查"入口**（相当于 strix `load_skill`）：
agent 现在只有 7 个工具，加第 8 个要过"工具面唯一"这条纪律（schema / 预算 / 审计同源）。
**留档不做**——它是"用起来更顺"，不是"少了这一条就不成立"。

### 4.1 复盘：为什么立项时会误判三批

矩阵是照 **strix 的目录结构**写的，没有先 grep 我们自己的实现。
于是"他们有一个叫 output_store 的模块"被直接读成"我们缺这个能力"，
而事实上我们是同一个能力换了名字、换了位置，还做得更细。

**收敛规则（写进流程）**：矩阵里每写一行"抄 X"，必须先在同一行填一格
「我们现在对应的东西」。填不出来的才是缺口；填得出来的，只记对比结论，不立批次。

---

## 5. 与既有断言的关系

本卷不新增 A 系列断言（A1–A20 属 `plan-veripen` 冻结包）。
B86 的判据落在 `tests/unit/test_discovery_crawl.py`（14 条），
B87 落在 `tests/unit/test_coverage.py`（11 条），
B89 落在 `tests/unit/test_spec_detect.py`（12 条）。
本批顺带把仓库的 `mypy --strict` 清零——`tests/routes/test_l0.py::test_l0_06` 在此之前是红的。

**回填口径**：`H01 V4` 已补"crawl 来源真实现（0.70）+ 覆盖账本双源"，
真实目标召回仍待复测。

---

## 6. 风险

| 风险 | 后果 | 处置 |
|---|---|---|
| 抄进来但没接闸门 | 发现阶段成为审计盲区 | `send_body` 与 `send` 共用 `_dispatch`；新增出网只许走这条 |
| 归一过度 | 两条不同的面撞成一个 ref，胶囊与报告锚错 | 只用三条规则；术语段一律不当动态 |
| 归一不足 | 面数爆炸，覆盖率分母失真 | 已修 CMS 文章号；语义 id 留给 B87 的账本去暴露 |
| 抄 strix 时顺手抄了它的判定 | 退化到"不可 replay" | 矩阵 §2.4 已列死；review 时只认 `H05` 的边界 |
| Apache-2.0 归属缺失 | 交付物有许可瑕疵 | 每个移植文件头注 `来源: usestrix/strix@6b7ae2e` |

---

## 7. 一句话收尾

> strix 值得抄的是**"在隔离环境里真的动手"**这条工程线；
> 我们把它的观察面抄成了 `discover`，把它的账本抄成了 `coverage.json`——
> 而判定那一层，一行都不动。
