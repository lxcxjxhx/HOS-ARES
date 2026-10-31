# MANIFEST · plan-veripen v3.0

> 复制后校验完整性：文件数、字节数、sha256 三项都对得上，才算拷对。
> **本文件不含自身哈希**（自指不可校验），除此之外全部文件都在表里。

| 项 | 值 |
|---|---|
| 包版本 | `plan-v3.0` |
| 生成时间 | 2026-10-30T15:51:39.576Z |
| 文件数 | 44 |
| 总字节 | 574285 |
| 总行数（文本） | 10382 |
| 内容 | 28 篇规格（含 27/28）+ README + 3 个自检脚本 + allowlist + 5 份 JSON Schema |

## 校验命令

```bash
node check-plan.cjs        # 结构 + 交叉引用 + schema 可解析 + 零地址闸门 + 版本一致
node check-noroute.cjs     # 单独跑零地址闸门（最后一行应为 OK: 0 命中）
node _verify-manifest.cjs  # 逐个文件比对 sha256
```

## 文件清单

| 文件 | 字节 | 行 | sha256（前 16） |
|---|---:|---:|---|
| `00-总规划.md` | 15246 | 197 | `bc59a1e934658524` |
| `01-包结构与内核契约.md` | 18505 | 321 | `6adbd4834190c91b` |
| `02-客户档Profile规格.md` | 10773 | 207 | `ab71196cfbb509ab` |
| `03-数据模型与状态机.md` | 16679 | 322 | `bfd3b41266378c50` |
| `04-调度规格.md` | 11575 | 208 | `4bb023e615f6bf9f` |
| `05-安全边界规格.md` | 18290 | 291 | `b485a615829db65e` |
| `06-证据与replay规格.md` | 19852 | 381 | `201d6a2b8c2990f0` |
| `07-Oracle规格.md` | 27002 | 429 | `e9a9d0dcc57804fc` |
| `08-Agent循环规格.md` | 20450 | 324 | `cc89a4cd464d07bd` |
| `09-报告与交付格式.md` | 11644 | 212 | `bc54e7a711a93688` |
| `10-评测协议.md` | 10274 | 173 | `dbbc6514c205a2d9` |
| `11-测试策略与靶场用例.md` | 20315 | 297 | `c93181437cc7af6d` |
| `12-工程与交付.md` | 12271 | 204 | `d0f4bee0c5d0bb97` |
| `13-风险登记册.md` | 10519 | 99 | `8828a634ea494f0e` |
| `14-二期路线.md` | 9734 | 169 | `19447eec8e6b6f27` |
| `15-开工提示词.md` | 12435 | 228 | `068383699a9d61ae` |
| `16-完整范围与交付地图.md` | 10276 | 149 | `d509cd300ce22995` |
| `17-权威链与证据门.md` | 15291 | 207 | `3e1edb0924cc28b2` |
| `18-Oracle目录（14类判定矩阵）.md` | 17113 | 255 | `18491dbef9ecd592` |
| `19-接口契约与用例生成.md` | 15484 | 266 | `709eabbffff94ce6` |
| `20-认证体系测试点.md` | 11962 | 215 | `75f0075b1270da36` |
| `21-实现批次与Agent执行计划.md` | 16563 | 215 | `8235f26f4a5ca4c8` |
| `22-产品与人机协同.md` | 11954 | 190 | `871027e0080292ff` |
| `23-可观测性与诊断.md` | 8283 | 148 | `22dbe3887077a999` |
| `24-服务API与错误码.md` | 16230 | 247 | `e3418ad14398b94f` |
| `25-靶场与真实数据规格.md` | 14603 | 232 | `55b236dc8295a90b` |
| `26-数据真实性与来源政策.md` | 9491 | 201 | `3812b8615a133081` |
| `27-无硬路由架构.md` | 33149 | 574 | `b114ed6105072534` |
| `28-逆向对标与借鉴规格.md` | 26220 | 385 | `f91dab0eb2b99ae9` |
| `29-逆向提取结果.md` | 15630 | 261 | `a4bc84f06884fd2c` |
| `30-对标绿盟HARNESS与开源生态.md` | 18915 | 311 | `aa8d26bd53d2447a` |
| `31-Playbook规格.md` | 9393 | 195 | `fc7b0d25603364c2` |
| `99-二期池.md` | 8270 | 123 | `52b3227892b5ec75` |
| `README.md` | 15417 | 259 | `350697b2ff739636` |
| `_gen_manifest.cjs` | 2123 | 66 | `4cbc8c2966c54e80` |
| `_verify-manifest.cjs` | 738 | 17 | `c018f719d48e7e94` |
| `check-noroute.cjs` | 8661 | 227 | `e3d619d401ab70f4` |
| `check-plan.cjs` | 5435 | 129 | `5152008c44eb82de` |
| `noroute.allowlist` | 471 | - | `87b76fdd98e832f6` |
| `schemas/capsule.schema.json` | 13223 | 561 | `d2acab9eed27cf79` |
| `schemas/dataset-manifest.schema.json` | 3037 | 139 | `ac90d5b9d54b4b4b` |
| `schemas/finding.schema.json` | 3262 | 78 | `57515f2decd4bae8` |
| `schemas/profile.schema.json` | 13722 | 574 | `60cb09f88a8407d0` |
| `schemas/routeset.schema.json` | 3805 | 96 | `cf94747e7962eda1` |

## 完整 sha256

```
bc59a1e9346585244df036241d2435da2e093fe7c2af905e757549521bdb8fca  00-总规划.md
6adbd4834190c91b8dab18092b2a485030f14033fa9cfbf265b47757c03975ec  01-包结构与内核契约.md
ab71196cfbb509ab1d05e40c2e2dd67446c9cfaa5761cb7547727e629262d1be  02-客户档Profile规格.md
bfd3b41266378c507b33d4433c72647ba613e658374dd3c2ba1278f8b8bb119f  03-数据模型与状态机.md
4bb023e615f6bf9f6e8f6cf251a4c74c5a0a5bf7146e3fb31d823a9a050a3bc0  04-调度规格.md
b485a615829db65ecfd94a3f1bbd1a7ab5891bb6b914021ad496985acd908934  05-安全边界规格.md
201d6a2b8c2990f01946e06da531d9cc3ddfb6cd92d037e30c5156a14d531d3f  06-证据与replay规格.md
e9a9d0dcc57804fcc0892d9cc53940778d8d7e6a5bd5803a07429c9caba3879e  07-Oracle规格.md
cc89a4cd464d07bd21595f5736201bed5691568f0776ac795088e60eb7244cee  08-Agent循环规格.md
bc54e7a711a93688ef3e0e9d71a898aba5c0140ebbf1fd5ffcf49bfc9ffb182a  09-报告与交付格式.md
dbbc6514c205a2d968039b5af16b0b5c1ba7ab1171c6396279aed4ab3b79ff7a  10-评测协议.md
c93181437cc7af6d74da691813fd8b8c1b14ba4264c99370f652dda798a191cc  11-测试策略与靶场用例.md
d0f4bee0c5d0bb97ba588ae0266cdc81694fb46270b1f9488cbe4486ba1a983c  12-工程与交付.md
8828a634ea494f0e49c5379d41065b8b5efe9d38da91f9387bf923f8a0032437  13-风险登记册.md
19447eec8e6b6f2742153ab6dd6001b1f08ec5e585d48b4b8ccaf985f7ece771  14-二期路线.md
068383699a9d61ae98392165ee2dcb0aa5fa74955ba8400bef7e311e7c7657da  15-开工提示词.md
d509cd300ce22995a2227142da9985dce59c91884de3bbce20a03869628ea3f3  16-完整范围与交付地图.md
3e1edb0924cc28b2d3a01957a71ef3752bf87ff686ee3e1599fc4e0bf8cca1d2  17-权威链与证据门.md
18491dbef9ecd5929387105ed596632d8a2457ebb8b48cd43da8e170deea79b6  18-Oracle目录（14类判定矩阵）.md
709eabbffff94ce6f18d5c390579e2344add4ba4127953c188d6b80df00444f1  19-接口契约与用例生成.md
75f0075b1270da362ed4c06c3c0f218179e39a65d1330aafb14095c4621e4103  20-认证体系测试点.md
8235f26f4a5ca4c8b0d22a5bbebde05217a6afef3fc78868ab46c0d5c1d7bd80  21-实现批次与Agent执行计划.md
871027e0080292ffdf5f5b8b2b3132db63327c461ca07502d64836e2968fd494  22-产品与人机协同.md
22dbe3887077a99980064988aec343d77d1c701431581119762df327f573440d  23-可观测性与诊断.md
e3418ad14398b94f5081adf8f1d20eb713a5530b5fcf68cc88d5fc0e28fb26cc  24-服务API与错误码.md
55b236dc8295a90ba77002227723276e1aaa95940f4c080e6933915af5676377  25-靶场与真实数据规格.md
3812b8615a133081dd1c48d0dd6dce011659e4ad11210c898fe24477ed532cd5  26-数据真实性与来源政策.md
b114ed61050725349c9c89c09ca6547c2e67ed7bbe6ebb4bf397a180cf31d3a0  27-无硬路由架构.md
f91dab0eb2b99ae9b7eb0f2e0d183fb43314f75491bb7a31b829c7b9307c825c  28-逆向对标与借鉴规格.md
a4bc84f06884fd2c7f159c76c9337a355025d51918d7442de0d15cf02416b6ee  29-逆向提取结果.md
aa8d26bd53d2447abb36dee69d0e5e654b6a64929ecfb7944ec08e6f9c6c9e54  30-对标绿盟HARNESS与开源生态.md
fc7b0d25603364c21b531ca9b074d643c9fff11670c811ec81087972b5c61be6  31-Playbook规格.md
52b3227892b5ec756e94e9a5a3aaacd13e004eba04a78f04c0aef08925fba73d  99-二期池.md
350697b2ff7396366275db83af855f2303e6d1ec0656b8408cee69cc926a2c91  README.md
4cbc8c2966c54e80e747bba620b27c7da89cbe24865371829ec2b6125468e8fd  _gen_manifest.cjs
c018f719d48e7e9432728394ddf5d9253a69feaf52cbf18cd24fe830fb9ae2ab  _verify-manifest.cjs
e3d619d401ab70f47136de9dd2d22057060e8de2a7642b88db9355b75bc0c5f4  check-noroute.cjs
5152008c44eb82debd5e23bbaa7794f3f903449c13758435b83b41b60f6d37fd  check-plan.cjs
87b76fdd98e832f63d46bfa727c1cf6fa8cb6cc7706697257bb75769cb3f7091  noroute.allowlist
d2acab9eed27cf79af211a2065dd26b6e54175673384d19101779c2f97aba11a  schemas/capsule.schema.json
ac90d5b9d54b4b4b02aacf2261fb40e85505cd8b90949003f68da61113d33f39  schemas/dataset-manifest.schema.json
57515f2decd4bae8fefa2ce3b9e5bd6be2f9ee84c9fee62f20c3e204d85d2591  schemas/finding.schema.json
60cb09f88a8407d000bdae47d117aee04c6e9508df678c0e7bedb04c06178cab  schemas/profile.schema.json
cf94747e7962eda1e6ca9d7cb239c175eb3974eb00bfa9e90517e06153d07bd0  schemas/routeset.schema.json
```
