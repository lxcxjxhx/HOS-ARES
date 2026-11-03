# MANIFEST · plan-veripen v3.6

> 复制后校验完整性：文件数、字节数、sha256 三项都对得上，才算拷对。
> **本文件不含自身哈希**（自指不可校验），除此之外全部文件都在表里。

| 项 | 值 |
|---|---|
| 包版本 | `plan-v3.6` |
| 生成时间 | 2026-11-03T09:04:51.295Z |
| 文件数 | 54 |
| 总字节 | 788375 |
| 总行数（文本） | 13597 |
| 内容 | 41 篇规格（含 27–39）+ 37 批计划 + README + 3 个自检脚本 + allowlist + 6 份 JSON Schema |

## 校验命令

```bash
node check-plan.cjs        # 结构 + 交叉引用 + schema 可解析 + 零地址闸门 + 标签一致 + 版本一致
node check-noroute.cjs     # 单独跑零地址闸门（最后一行应为 OK: 0 命中）
node _verify-manifest.cjs  # 逐个文件比对 sha256（★ 内容改过就必须重跑本脚本生成）
```

## 文件清单

| 文件 | 字节 | 行 | sha256（前 16） |
|---|---:|---:|---|
| `00-总规划.md` | 23520 | 258 | `b723baec6002b007` |
| `01-包结构与内核契约.md` | 20077 | 348 | `b030e90f6094b86e` |
| `02-客户档Profile规格.md` | 10567 | 207 | `bf5eedd22d2b51dc` |
| `03-数据模型与状态机.md` | 23197 | 423 | `f6ecbae511ad7a2f` |
| `04-调度规格.md` | 14431 | 239 | `b075110cbedd6ddd` |
| `05-安全边界规格.md` | 19927 | 319 | `9727cbb2d416c486` |
| `06-证据与replay规格.md` | 19472 | 381 | `35b671b9fd9d4c7e` |
| `07-Oracle规格.md` | 26574 | 429 | `17c1e80ac6f6795b` |
| `08-Agent循环规格.md` | 23751 | 363 | `b093f7011dd0da7e` |
| `09-报告与交付格式.md` | 14706 | 251 | `ea47497785171358` |
| `10-评测协议.md` | 12129 | 196 | `4ea04e002a809d9a` |
| `11-测试策略与靶场用例.md` | 20019 | 297 | `244b27710dcb0c6e` |
| `12-工程与交付.md` | 12068 | 204 | `9308ad7a2c76edb3` |
| `13-风险登记册.md` | 14233 | 108 | `d382ef96464c0d0c` |
| `14-二期路线.md` | 10779 | 173 | `4cb8529ec1e9c1ed` |
| `15-开工提示词.md` | 12435 | 228 | `7415369f8780ef7e` |
| `16-完整范围与交付地图.md` | 15040 | 186 | `e3c28f1571985572` |
| `17-权威链与证据门.md` | 17495 | 213 | `da3464b2123236c3` |
| `18-Oracle目录（14类判定矩阵）.md` | 16859 | 255 | `f9e475a3ae885603` |
| `19-接口契约与用例生成.md` | 15219 | 266 | `0de44058b5c3655b` |
| `20-认证体系测试点.md` | 11748 | 215 | `d0e1c4cbd7d532d1` |
| `21-实现批次与Agent执行计划.md` | 48130 | 457 | `784ea5d6fa45b2e7` |
| `22-产品与人机协同.md` | 11765 | 190 | `e5113d77af7ec200` |
| `23-可观测性与诊断.md` | 8283 | 148 | `22dbe3887077a999` |
| `24-服务API与错误码.md` | 15984 | 247 | `8d35375f222029df` |
| `25-靶场与真实数据规格.md` | 14372 | 232 | `fad3e5a4fade2827` |
| `26-数据真实性与来源政策.md` | 9491 | 201 | `3812b8615a133081` |
| `27-无硬路由架构.md` | 32576 | 574 | `994957860f637c39` |
| `28-逆向对标与借鉴规格.md` | 26286 | 385 | `1e9a71b710b7020d` |
| `29-逆向提取结果.md` | 15370 | 261 | `0c1d61f9f05baeec` |
| `30-对标绿盟HARNESS与开源生态.md` | 20560 | 322 | `b01329f67c60e2f4` |
| `31-Playbook规格.md` | 9393 | 195 | `fc7b0d25603364c2` |
| `32-竞品复检与开源映射.md` | 21085 | 255 | `483928e4324bef4f` |
| `33-Harness架构总图与装配规格.md` | 10644 | 169 | `ed0eb6084ef9b18b` |
| `34-Harness操作台规格.md` | 9084 | 154 | `8699a9fcfa889f25` |
| `35-自主渗透与证据闭环规格.md` | 10624 | 212 | `01c278a0754d541c` |
| `36-插件市场与能力发现规格.md` | 18875 | 389 | `8014e0a930501569` |
| `37-三样本逆向提取结果.md` | 15149 | 297 | `aaa0365f17ee0924` |
| `38-通用工作台规格.md` | 12817 | 282 | `d5ee9cb802e5dc04` |
| `39-交付收尾与能力接驳规格.md` | 14279 | 206 | `d138e94b51a03f9e` |
| `40-验证方案与批次规划.md` | 10208 | 215 | `cd2b2c8327f5b20b` |
| `99-二期池.md` | 10698 | 130 | `7a572865feedb8a3` |
| `README.md` | 19615 | 299 | `476aa8519e2ad11f` |
| `_gen_manifest.cjs` | 2229 | 66 | `4dcd3362e8c0afc0` |
| `_verify-manifest.cjs` | 738 | 17 | `c018f719d48e7e94` |
| `check-noroute.cjs` | 9235 | 234 | `2ca8872e0d14d858` |
| `check-plan.cjs` | 11013 | 237 | `4fe8f2da77e60d60` |
| `noroute.allowlist` | 651 | - | `d12fde747f6a1720` |
| `schemas/capsule.schema.json` | 12663 | 561 | `a36899146f43f91e` |
| `schemas/dataset-manifest.schema.json` | 3037 | 139 | `ac90d5b9d54b4b4b` |
| `schemas/finding.schema.json` | 5485 | 124 | `6fd62e7981d0b096` |
| `schemas/playbook.schema.json` | 6263 | 170 | `3fef0db628c9173d` |
| `schemas/profile.schema.json` | 13722 | 574 | `60cb09f88a8407d0` |
| `schemas/routeset.schema.json` | 3805 | 96 | `cf94747e7962eda1` |

## 完整 sha256

```
b723baec6002b0079a583a43301c07ca5809a18dab00e486839bf6534536efe4  00-总规划.md
b030e90f6094b86e0b086856be477aca6a0ba636901e944dbd06b7057fac06a1  01-包结构与内核契约.md
bf5eedd22d2b51dc368b35b323a90ac85c184f5d33b753de292fb9f5ccba1150  02-客户档Profile规格.md
f6ecbae511ad7a2f2f27b08ec428adda839c0905ed02911941bbd66dc5bb4987  03-数据模型与状态机.md
b075110cbedd6ddd55476ee1a2ee012ced2122313a710cec0552f91559bf771e  04-调度规格.md
9727cbb2d416c486717a7ae89651afe6a5e6bb549ad624721db905dd147e2e6e  05-安全边界规格.md
35b671b9fd9d4c7e8393382400925b5226e8d625b82cc494ddd0bb3a11e7715d  06-证据与replay规格.md
17c1e80ac6f6795bb991740215e1bab217c08acdb3d7e1085f318a19ada23106  07-Oracle规格.md
b093f7011dd0da7e3bbc500845c53e08c71344cd78218eb6d371f88a09b8150e  08-Agent循环规格.md
ea474977851713587de8dd05a7a5d82579362f9178c631f401e034b0ee0d7b65  09-报告与交付格式.md
4ea04e002a809d9acafbec059a1f87fe2ed20ec22c94ab6ab8048293cf2cb93d  10-评测协议.md
244b27710dcb0c6e772436b17ccda94a938531255d54298fd4d9402172a3a616  11-测试策略与靶场用例.md
9308ad7a2c76edb334249648831aed51ba725d39497632b4b6d2c71fe637a0f4  12-工程与交付.md
d382ef96464c0d0c991af6d2a6f648c046df4a1ee9162c3e5ce52d308bd04175  13-风险登记册.md
4cb8529ec1e9c1edb6ada7ec37a872bf077c44aec532eb9eec79fbaf39329dd5  14-二期路线.md
7415369f8780ef7eadb6de88d089a90d24ac74ae4f967a77cee30f25f13e34ce  15-开工提示词.md
e3c28f15719855725ce2b3b634202b247b302b3578c7b3e90b2bdba7b2a77241  16-完整范围与交付地图.md
da3464b2123236c30f46c436bd575842b19f5861b1605aaba5aab3d81b879e39  17-权威链与证据门.md
f9e475a3ae885603117af26e0c2799083e6e6bdb4c9785ad37463b3b14423c82  18-Oracle目录（14类判定矩阵）.md
0de44058b5c3655bce8909c43b2171f100069c3b4a15f0b2cb75f322b1787b32  19-接口契约与用例生成.md
d0e1c4cbd7d532d108567a4551cf3d5671809db1a5f1fadd4aa7454d44fcb086  20-认证体系测试点.md
784ea5d6fa45b2e705f86f51114bc2b8f97a714e5f4bc47d4ccf84a8dc59316c  21-实现批次与Agent执行计划.md
e5113d77af7ec200a808f45d6b0c5a6588bbd1cc537423a9bc93664c80852f27  22-产品与人机协同.md
22dbe3887077a99980064988aec343d77d1c701431581119762df327f573440d  23-可观测性与诊断.md
8d35375f222029df1eadb22a2b9ed311dfed61d5cd86f7395e479dd4385c7604  24-服务API与错误码.md
fad3e5a4fade2827def2b960b31cbf647c1376aca6504c25aaf176d6cc4f3c67  25-靶场与真实数据规格.md
3812b8615a133081dd1c48d0dd6dce011659e4ad11210c898fe24477ed532cd5  26-数据真实性与来源政策.md
994957860f637c39f6b8a82bbefe70c84c868050d21d74bacecdf65c789861e3  27-无硬路由架构.md
1e9a71b710b7020d570165d87212bb1630af5273bc53a4b1d6811c3500e28d2a  28-逆向对标与借鉴规格.md
0c1d61f9f05baeec5d7d9fa0f07ee0c4e2179aa9a9410ad656597c9cebcd57d1  29-逆向提取结果.md
b01329f67c60e2f47475e35389e0b81bbf921abff4a0e492e6794bbb1bd3b7c6  30-对标绿盟HARNESS与开源生态.md
fc7b0d25603364c21b531ca9b074d643c9fff11670c811ec81087972b5c61be6  31-Playbook规格.md
483928e4324bef4f1a298a4161f541ce57499b1d3d3d6b723206c587d7f91bf3  32-竞品复检与开源映射.md
ed0eb6084ef9b18b390c293636366e24f52d6f77ca4c8dc8831fcaa712f48ea6  33-Harness架构总图与装配规格.md
8699a9fcfa889f2590983f8c9ab1ad8eb4e33d29b5cb4ec67c64231815b8afbf  34-Harness操作台规格.md
01c278a0754d541c52a7cfa19513fc81be51a595d185d99468992ddfe8f4aad4  35-自主渗透与证据闭环规格.md
8014e0a930501569dcb8f315c9860f0f9586258256030b4cd7d42c1d41c9bb25  36-插件市场与能力发现规格.md
aaa0365f17ee0924f17eb2ed6afcdb4ac6728426c638cc5a3e705208d2ea2dbd  37-三样本逆向提取结果.md
d5ee9cb802e5dc04db6cd9a57cd0d60f64547a4e143b123ddee826ae1cc18063  38-通用工作台规格.md
d138e94b51a03f9ed6107bf26d44f54327565ff31bc6e61497f7f3f5b9315e9a  39-交付收尾与能力接驳规格.md
cd2b2c8327f5b20b5b9fd3f61895594f597b6d970e278bd170bf5e58bdeebdbf  40-验证方案与批次规划.md
7a572865feedb8a31696028b7f7563e66af7db7ddf344ad845f4719ea22f4457  99-二期池.md
476aa8519e2ad11f62671ce4af00a8fd0855b0efabee27693433b935b19fd089  README.md
4dcd3362e8c0afc09641f99f90d4fbffb5a53bda55251ca2e140d4649e93c600  _gen_manifest.cjs
c018f719d48e7e9432728394ddf5d9253a69feaf52cbf18cd24fe830fb9ae2ab  _verify-manifest.cjs
2ca8872e0d14d8581b75c2138ad7a265e6d3fe98b5a9b88196da5923839dfec1  check-noroute.cjs
4fe8f2da77e60d605e4af2dce9bc9aea9b510c77f4454021b62c9eb50e7cc578  check-plan.cjs
d12fde747f6a17209fa0190936fcd98ed8bb8e1fdd2453fa5f6b119819a899d5  noroute.allowlist
a36899146f43f91ee1f55e80ed8e7e349c638954cbecd0ea08a4f1af46fcd8c7  schemas/capsule.schema.json
ac90d5b9d54b4b4b02aacf2261fb40e85505cd8b90949003f68da61113d33f39  schemas/dataset-manifest.schema.json
6fd62e7981d0b09699fc2dce241ff216bc2ba26c4b985e76ce75f7db777a3c89  schemas/finding.schema.json
3fef0db628c9173d17837caa1542322714f1dde7d402e9a279fe8f93675a798a  schemas/playbook.schema.json
60cb09f88a8407d000bdae47d117aee04c6e9508df678c0e7bedb04c06178cab  schemas/profile.schema.json
cf94747e7962eda1e6ca9d7cb239c175eb3974eb00bfa9e90517e06153d07bd0  schemas/routeset.schema.json
```
