# MANIFEST · plan-hos-ares v3.6

> 复制后校验完整性：文件数、字节数、sha256 三项都对得上，才算拷对。
> **本文件不含自身哈希**（自指不可校验），除此之外全部文件都在表里。

| 项 | 值 |
|---|---|
| 包版本 | `plan-v3.9` |
| 生成时间 | 2026-11-18T13:30:50.182Z |
| 文件数 | 54 |
| 总字节 | 788230 |
| 总行数（文本） | 13656 |
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
| `00-总规划.md` | 23182 | 258 | `281edbd2a46dc3bc` |
| `01-包结构与内核契约.md` | 19749 | 348 | `8a2c46090cc7406f` |
| `02-客户档Profile规格.md` | 10571 | 207 | `acf5256bba4c9448` |
| `03-数据模型与状态机.md` | 22775 | 423 | `5522b0492ffded47` |
| `04-调度规格.md` | 14431 | 239 | `b075110cbedd6ddd` |
| `05-安全边界规格.md` | 19615 | 319 | `45c1bbcd114e559d` |
| `06-证据与replay规格.md` | 19828 | 384 | `37023fcf3a031106` |
| `07-Oracle规格.md` | 26576 | 429 | `7a85cb1aa11b8e0a` |
| `08-Agent循环规格.md` | 23390 | 363 | `7edb1ffe5d94eaed` |
| `09-报告与交付格式.md` | 14720 | 251 | `21064099c4849da4` |
| `10-评测协议.md` | 11934 | 196 | `4852348624f9e271` |
| `11-测试策略与靶场用例.md` | 20026 | 297 | `3891006838ece92e` |
| `12-工程与交付.md` | 12073 | 204 | `8437871f2dcd9d8f` |
| `13-风险登记册.md` | 14709 | 109 | `7777faca12463a01` |
| `14-二期路线.md` | 10612 | 173 | `607f9d86051dd460` |
| `15-开工提示词.md` | 12218 | 228 | `e7ce3fd6d20ddb17` |
| `16-完整范围与交付地图.md` | 17168 | 206 | `086a1e41b6dd7047` |
| `17-权威链与证据门.md` | 17288 | 213 | `96abfa18cd83ab1d` |
| `18-Oracle目录（14类判定矩阵）.md` | 16860 | 255 | `bb304bbec47076c9` |
| `19-接口契约与用例生成.md` | 15224 | 266 | `9069ad9491f82355` |
| `20-认证体系测试点.md` | 11748 | 215 | `d0e1c4cbd7d532d1` |
| `21-实现批次与Agent执行计划.md` | 47767 | 457 | `7cd9a862c1279c8b` |
| `22-产品与人机协同.md` | 11766 | 190 | `3f2ddf26235b7593` |
| `23-可观测性与诊断.md` | 8283 | 148 | `22dbe3887077a999` |
| `24-服务API与错误码.md` | 16159 | 247 | `83596879c3e92e4f` |
| `25-靶场与真实数据规格.md` | 14374 | 232 | `4641455b5fd29b2b` |
| `26-数据真实性与来源政策.md` | 9494 | 201 | `e9998b6390ac4648` |
| `27-无硬路由架构.md` | 32581 | 574 | `75aaf37b7c13e4f2` |
| `28-逆向对标与借鉴规格.md` | 25904 | 385 | `11d3c8861b1470f6` |
| `29-逆向提取结果.md` | 15370 | 261 | `0c1d61f9f05baeec` |
| `30-对标绿盟HARNESS与开源生态.md` | 20242 | 322 | `ab65b355aa35f1d8` |
| `31-Playbook规格.md` | 9395 | 195 | `65d2fa093afcdf14` |
| `32-竞品复检与开源映射.md` | 20832 | 255 | `1acb36dd23ab7d63` |
| `33-Harness架构总图与装配规格.md` | 10656 | 169 | `7bbfe0efb16837b5` |
| `34-Harness操作台规格.md` | 9084 | 154 | `8699a9fcfa889f25` |
| `35-自主渗透与证据闭环规格.md` | 10624 | 212 | `01c278a0754d541c` |
| `36-插件市场与能力发现规格.md` | 18492 | 389 | `ec234ae3d197d8ba` |
| `37-三样本逆向提取结果.md` | 15149 | 297 | `aaa0365f17ee0924` |
| `38-通用工作台规格.md` | 12538 | 282 | `db87484114650f8b` |
| `39-交付收尾与能力接驳规格.md` | 14095 | 206 | `82591f197ddd2600` |
| `40-验证方案与批次规划.md` | 11619 | 238 | `d5c18db9e4867f97` |
| `99-二期池.md` | 10569 | 130 | `3997842d8d4361b7` |
| `README.md` | 19518 | 302 | `b2799864ec36b387` |
| `_gen_manifest.cjs` | 2561 | 72 | `d1d01adcc0042479` |
| `_verify-manifest.cjs` | 1053 | 20 | `78ff83361bfb51ed` |
| `check-noroute.cjs` | 9002 | 234 | `b1fa93672fc4945b` |
| `check-plan.cjs` | 10778 | 237 | `f937c9f599aa141e` |
| `noroute.allowlist` | 639 | - | `37480026dc33d566` |
| `schemas/capsule.schema.json` | 12665 | 561 | `448f3312f2694449` |
| `schemas/dataset-manifest.schema.json` | 3039 | 139 | `07b4a68f439be172` |
| `schemas/finding.schema.json` | 5487 | 124 | `18f6120c6a07749b` |
| `schemas/playbook.schema.json` | 6266 | 170 | `2fbc79f2d90905af` |
| `schemas/profile.schema.json` | 13725 | 574 | `4cad7fa5dec8762d` |
| `schemas/routeset.schema.json` | 3807 | 96 | `b271dbce2e69f547` |

## 完整 sha256

```
281edbd2a46dc3bc5f17aa21cdf2bb346541d12c6e20f2a217aa10aae492f588  00-总规划.md
8a2c46090cc7406fe9bc3bf21b5cdf5b36b631ae1672322f06e46cc4e4a0a1d7  01-包结构与内核契约.md
acf5256bba4c9448ae01a8a8be2cf78bcbe66757ecf17dc59b559cb72baccc21  02-客户档Profile规格.md
5522b0492ffded471a5206ea6223b6db4094e6c5625c876c12ac54b984a02b19  03-数据模型与状态机.md
b075110cbedd6ddd55476ee1a2ee012ced2122313a710cec0552f91559bf771e  04-调度规格.md
45c1bbcd114e559d261d291f95566baa4fb08e5ebec317820a4d3a76377edca3  05-安全边界规格.md
37023fcf3a0311067c229b90ff34078e8be4ea2128cc6c3efd1650504145f612  06-证据与replay规格.md
7a85cb1aa11b8e0ace748dceca99cba0cf2193a007897512e0265e6031dfdb97  07-Oracle规格.md
7edb1ffe5d94eaedb87bcae9faa70e3b60fadac4bc869dfc53fd1de3a9de611f  08-Agent循环规格.md
21064099c4849da4131554f3614846835132e0eaa471fd35e78c088ac3b35e0c  09-报告与交付格式.md
4852348624f9e271fdef0bbc4f61bce2d88c3e68e7b71772a3ba3867f2176336  10-评测协议.md
3891006838ece92ea9f330dfbeb4e66625d18cea414ab1de157aee4f57c5b603  11-测试策略与靶场用例.md
8437871f2dcd9d8f33d2764e49db3a59a06665746c6c6651e3602f2de06d0a03  12-工程与交付.md
7777faca12463a01273b8b6b8f8a04fc07a7fe82a3009114839f3824104ee13b  13-风险登记册.md
607f9d86051dd46035b225a34e1bec10e7765288ba7bad6f1abdcadf7bd699f6  14-二期路线.md
e7ce3fd6d20ddb177ce456c9328b1aaf7d2c60b88cbd0f68761b9db4f87815be  15-开工提示词.md
086a1e41b6dd7047dcfaf8daba5e3fc115e1cb8a4f46879afd3afac0d7a103db  16-完整范围与交付地图.md
96abfa18cd83ab1dab8bdb9b1985d17e8b506eb493f06fe6bdd907ebdcafad8e  17-权威链与证据门.md
bb304bbec47076c9201b236085247604ce4f0802bd132f8b858513321c56ee58  18-Oracle目录（14类判定矩阵）.md
9069ad9491f82355bee9e6dede052ef3da0488cfa5f0a5af183492e461388654  19-接口契约与用例生成.md
d0e1c4cbd7d532d108567a4551cf3d5671809db1a5f1fadd4aa7454d44fcb086  20-认证体系测试点.md
7cd9a862c1279c8b48aa89d22f8cf2bba07b0da71efd68349ecf75ba44a61d48  21-实现批次与Agent执行计划.md
3f2ddf26235b759323178cc41224b13db561d5a36e57504cc7ba73c25765c707  22-产品与人机协同.md
22dbe3887077a99980064988aec343d77d1c701431581119762df327f573440d  23-可观测性与诊断.md
83596879c3e92e4f65c138565189dc1f04e1342dbd6b7bd97f95aeb3bf241dcd  24-服务API与错误码.md
4641455b5fd29b2b46bd7edf24f6338510a3d51a3b9e1134e369b87acccd453a  25-靶场与真实数据规格.md
e9998b6390ac4648a2a50eaa067bc64ddde5ec1cb7edd5356c27113a14600cec  26-数据真实性与来源政策.md
75aaf37b7c13e4f2b14f88beb9547d3528b3b5d0ed1533c643ae1ec5061b2a62  27-无硬路由架构.md
11d3c8861b1470f65e970bf46a61ac9a5559bbcd17ba33c171c24ea24f651c95  28-逆向对标与借鉴规格.md
0c1d61f9f05baeec5d7d9fa0f07ee0c4e2179aa9a9410ad656597c9cebcd57d1  29-逆向提取结果.md
ab65b355aa35f1d83714cbf5d973c1acc62e485090ff71e3188c0532f21e203a  30-对标绿盟HARNESS与开源生态.md
65d2fa093afcdf14fb2eecb02906c7e92632fc28c027f632686cbe134447fba3  31-Playbook规格.md
1acb36dd23ab7d63ee28aef1af4a7f1365ebf23e813a7cad003a4711702b989d  32-竞品复检与开源映射.md
7bbfe0efb16837b57b9a538aeb30e3b5c849d89e989e792c341358bf8737aca6  33-Harness架构总图与装配规格.md
8699a9fcfa889f2590983f8c9ab1ad8eb4e33d29b5cb4ec67c64231815b8afbf  34-Harness操作台规格.md
01c278a0754d541c52a7cfa19513fc81be51a595d185d99468992ddfe8f4aad4  35-自主渗透与证据闭环规格.md
ec234ae3d197d8ba45212f78aa0e65c3234baccc436824f055f00941d9b450ea  36-插件市场与能力发现规格.md
aaa0365f17ee0924f17eb2ed6afcdb4ac6728426c638cc5a3e705208d2ea2dbd  37-三样本逆向提取结果.md
db87484114650f8bf256015b0c7dc4542d236fdf128e77cfc0bee3ed3464ca14  38-通用工作台规格.md
82591f197ddd2600359132be6547619633f611a851d29366b3cc98d6e0400350  39-交付收尾与能力接驳规格.md
d5c18db9e4867f9747bfebe1aacbb9cae7df8f09ee1cb420eb8c518a4c67c994  40-验证方案与批次规划.md
3997842d8d4361b7a4f6befca5b3d23bb3f158eb09a431d5139edc2ac884bf4e  99-二期池.md
b2799864ec36b387d528c667b5168202d27f77d743f8ba8da5f05016f1978329  README.md
d1d01adcc0042479bccac5b48fdc63d4f880a33f1865b988e05472297d7c978d  _gen_manifest.cjs
78ff83361bfb51ed2283d860c620d0c5c2e378d978deec29dc18f43782d1450b  _verify-manifest.cjs
b1fa93672fc4945b24af980708e441569122240f2f3f9b4c51e6d0e1e53c442a  check-noroute.cjs
f937c9f599aa141ec52653779dcc7ca00210f8e2ec788f5ebdbb3b488ab86952  check-plan.cjs
37480026dc33d5668268f0b6c238ec0bb12be277f6b7623f33d216a3cfe81180  noroute.allowlist
448f3312f2694449a3f345620d6dd63e8fcd0705f7e830778c475bcb1e1a1113  schemas/capsule.schema.json
07b4a68f439be172cfd7dd237938b9e6968777a971cd4357bf4f8899dc4e72b6  schemas/dataset-manifest.schema.json
18f6120c6a07749bd073fe4d1b51c72fbf81a6e7b010915deb68fc35827dd157  schemas/finding.schema.json
2fbc79f2d90905af9fc29508b0f686bf3fe9f8745e53ac26411bfbcdcc68bba5  schemas/playbook.schema.json
4cad7fa5dec8762d4435d16e1ba492f7bb31044686bf986f34e44cf46f8d6495  schemas/profile.schema.json
b271dbce2e69f54775cc5a20f7b6d79d8ff1e1b5ba86bdfa3188c28193d775b8  schemas/routeset.schema.json
```
