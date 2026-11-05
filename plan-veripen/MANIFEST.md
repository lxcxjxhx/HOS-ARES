# MANIFEST · plan-veripen v3.6

> 复制后校验完整性：文件数、字节数、sha256 三项都对得上，才算拷对。
> **本文件不含自身哈希**（自指不可校验），除此之外全部文件都在表里。

| 项 | 值 |
|---|---|
| 包版本 | `plan-v3.9` |
| 生成时间 | 2026-11-05T12:10:41.586Z |
| 文件数 | 54 |
| 总字节 | 787425 |
| 总行数（文本） | 13653 |
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
| `00-总规划.md` | 23178 | 258 | `13fcd3018d550bc4` |
| `01-包结构与内核契约.md` | 19730 | 348 | `b68b40c38e38d970` |
| `02-客户档Profile规格.md` | 10567 | 207 | `bf5eedd22d2b51dc` |
| `03-数据模型与状态机.md` | 22775 | 423 | `5522b0492ffded47` |
| `04-调度规格.md` | 14431 | 239 | `b075110cbedd6ddd` |
| `05-安全边界规格.md` | 19609 | 319 | `c2ad9e3469bf3c3f` |
| `06-证据与replay规格.md` | 19472 | 381 | `35b671b9fd9d4c7e` |
| `07-Oracle规格.md` | 26574 | 429 | `17c1e80ac6f6795b` |
| `08-Agent循环规格.md` | 23389 | 363 | `af53ef5d77aab1ef` |
| `09-报告与交付格式.md` | 14706 | 251 | `ea47497785171358` |
| `10-评测协议.md` | 11934 | 196 | `4852348624f9e271` |
| `11-测试策略与靶场用例.md` | 20019 | 297 | `244b27710dcb0c6e` |
| `12-工程与交付.md` | 12068 | 204 | `9308ad7a2c76edb3` |
| `13-风险登记册.md` | 14707 | 109 | `b70841f3b4e04e36` |
| `14-二期路线.md` | 10607 | 173 | `0dcfb90ed5b8572d` |
| `15-开工提示词.md` | 12208 | 228 | `2a8ca8c01fe48ee9` |
| `16-完整范围与交付地图.md` | 17163 | 206 | `68b6825e10d71a19` |
| `17-权威链与证据门.md` | 17283 | 213 | `f7b628ee1609c134` |
| `18-Oracle目录（14类判定矩阵）.md` | 16859 | 255 | `f9e475a3ae885603` |
| `19-接口契约与用例生成.md` | 15219 | 266 | `0de44058b5c3655b` |
| `20-认证体系测试点.md` | 11748 | 215 | `d0e1c4cbd7d532d1` |
| `21-实现批次与Agent执行计划.md` | 47674 | 457 | `24cf9d5de0c30129` |
| `22-产品与人机协同.md` | 11765 | 190 | `e5113d77af7ec200` |
| `23-可观测性与诊断.md` | 8283 | 148 | `22dbe3887077a999` |
| `24-服务API与错误码.md` | 15984 | 247 | `8d35375f222029df` |
| `25-靶场与真实数据规格.md` | 14372 | 232 | `fad3e5a4fade2827` |
| `26-数据真实性与来源政策.md` | 9491 | 201 | `3812b8615a133081` |
| `27-无硬路由架构.md` | 32576 | 574 | `994957860f637c39` |
| `28-逆向对标与借鉴规格.md` | 25902 | 385 | `f9c3e4a54630f39b` |
| `29-逆向提取结果.md` | 15370 | 261 | `0c1d61f9f05baeec` |
| `30-对标绿盟HARNESS与开源生态.md` | 20239 | 322 | `64334d7785126286` |
| `31-Playbook规格.md` | 9393 | 195 | `fc7b0d25603364c2` |
| `32-竞品复检与开源映射.md` | 20831 | 255 | `f861d84c715a073c` |
| `33-Harness架构总图与装配规格.md` | 10644 | 169 | `ed0eb6084ef9b18b` |
| `34-Harness操作台规格.md` | 9084 | 154 | `8699a9fcfa889f25` |
| `35-自主渗透与证据闭环规格.md` | 10624 | 212 | `01c278a0754d541c` |
| `36-插件市场与能力发现规格.md` | 18487 | 389 | `053a7281e024906f` |
| `37-三样本逆向提取结果.md` | 15149 | 297 | `aaa0365f17ee0924` |
| `38-通用工作台规格.md` | 12536 | 282 | `e2af9163080c52c4` |
| `39-交付收尾与能力接驳规格.md` | 14074 | 206 | `a351623dad68361f` |
| `40-验证方案与批次规划.md` | 11617 | 238 | `0dc2fd4b2d24faf2` |
| `99-二期池.md` | 10569 | 130 | `3997842d8d4361b7` |
| `README.md` | 19509 | 302 | `7cb7143f6f26087e` |
| `_gen_manifest.cjs` | 2560 | 72 | `9388bb9309746c84` |
| `_verify-manifest.cjs` | 1053 | 20 | `78ff83361bfb51ed` |
| `check-noroute.cjs` | 9002 | 234 | `b1fa93672fc4945b` |
| `check-plan.cjs` | 10777 | 237 | `470ea1d45420c31c` |
| `noroute.allowlist` | 639 | - | `37480026dc33d566` |
| `schemas/capsule.schema.json` | 12663 | 561 | `a36899146f43f91e` |
| `schemas/dataset-manifest.schema.json` | 3037 | 139 | `ac90d5b9d54b4b4b` |
| `schemas/finding.schema.json` | 5485 | 124 | `6fd62e7981d0b096` |
| `schemas/playbook.schema.json` | 6263 | 170 | `3fef0db628c9173d` |
| `schemas/profile.schema.json` | 13722 | 574 | `60cb09f88a8407d0` |
| `schemas/routeset.schema.json` | 3805 | 96 | `cf94747e7962eda1` |

## 完整 sha256

```
13fcd3018d550bc49e64ef0639657b31b7b199c71de338ff570c62749381e831  00-总规划.md
b68b40c38e38d9703c185669788b5faa1346eb518b757e5801192796d43a1837  01-包结构与内核契约.md
bf5eedd22d2b51dc368b35b323a90ac85c184f5d33b753de292fb9f5ccba1150  02-客户档Profile规格.md
5522b0492ffded471a5206ea6223b6db4094e6c5625c876c12ac54b984a02b19  03-数据模型与状态机.md
b075110cbedd6ddd55476ee1a2ee012ced2122313a710cec0552f91559bf771e  04-调度规格.md
c2ad9e3469bf3c3f0d2fb1bcba3c1e46f9ba0e6dc7c2e655792d2d991c6c95bb  05-安全边界规格.md
35b671b9fd9d4c7e8393382400925b5226e8d625b82cc494ddd0bb3a11e7715d  06-证据与replay规格.md
17c1e80ac6f6795bb991740215e1bab217c08acdb3d7e1085f318a19ada23106  07-Oracle规格.md
af53ef5d77aab1ef1110f27deb594cb91a8e4cfe9bfa99f7bad189e3d9d710fc  08-Agent循环规格.md
ea474977851713587de8dd05a7a5d82579362f9178c631f401e034b0ee0d7b65  09-报告与交付格式.md
4852348624f9e271fdef0bbc4f61bce2d88c3e68e7b71772a3ba3867f2176336  10-评测协议.md
244b27710dcb0c6e772436b17ccda94a938531255d54298fd4d9402172a3a616  11-测试策略与靶场用例.md
9308ad7a2c76edb334249648831aed51ba725d39497632b4b6d2c71fe637a0f4  12-工程与交付.md
b70841f3b4e04e36fa38f9af169d015c45a060b3562e8d8d11d3b91cc67c8179  13-风险登记册.md
0dcfb90ed5b8572de11bc672440ee0daade38fda32c437b40929d16e83a4b646  14-二期路线.md
2a8ca8c01fe48ee9f136d2d3e9c70b57b6de3c60ad5d84d68e2c826c032ad537  15-开工提示词.md
68b6825e10d71a19dc582941a47c9a4fb0df3fac9cf5dfcea769f07fe378474b  16-完整范围与交付地图.md
f7b628ee1609c1344ab825b1f5fd0d535af7bf7b9716d8160919c87490c56c2c  17-权威链与证据门.md
f9e475a3ae885603117af26e0c2799083e6e6bdb4c9785ad37463b3b14423c82  18-Oracle目录（14类判定矩阵）.md
0de44058b5c3655bce8909c43b2171f100069c3b4a15f0b2cb75f322b1787b32  19-接口契约与用例生成.md
d0e1c4cbd7d532d108567a4551cf3d5671809db1a5f1fadd4aa7454d44fcb086  20-认证体系测试点.md
24cf9d5de0c30129042c7ecfdfbc47cdd002570827422df80af5ac94b5bbfcd3  21-实现批次与Agent执行计划.md
e5113d77af7ec200a808f45d6b0c5a6588bbd1cc537423a9bc93664c80852f27  22-产品与人机协同.md
22dbe3887077a99980064988aec343d77d1c701431581119762df327f573440d  23-可观测性与诊断.md
8d35375f222029df1eadb22a2b9ed311dfed61d5cd86f7395e479dd4385c7604  24-服务API与错误码.md
fad3e5a4fade2827def2b960b31cbf647c1376aca6504c25aaf176d6cc4f3c67  25-靶场与真实数据规格.md
3812b8615a133081dd1c48d0dd6dce011659e4ad11210c898fe24477ed532cd5  26-数据真实性与来源政策.md
994957860f637c39f6b8a82bbefe70c84c868050d21d74bacecdf65c789861e3  27-无硬路由架构.md
f9c3e4a54630f39b7c766f6e96413ad222107bd29ef81ef2e55946e5a6e4ef3b  28-逆向对标与借鉴规格.md
0c1d61f9f05baeec5d7d9fa0f07ee0c4e2179aa9a9410ad656597c9cebcd57d1  29-逆向提取结果.md
64334d77851262865bdfa918aebe28fc34f194ac5a1574a0d18d57866e95f342  30-对标绿盟HARNESS与开源生态.md
fc7b0d25603364c21b531ca9b074d643c9fff11670c811ec81087972b5c61be6  31-Playbook规格.md
f861d84c715a073cda3bd46ef4312c06c1de041710b9508f0bc0294fd6d6857c  32-竞品复检与开源映射.md
ed0eb6084ef9b18b390c293636366e24f52d6f77ca4c8dc8831fcaa712f48ea6  33-Harness架构总图与装配规格.md
8699a9fcfa889f2590983f8c9ab1ad8eb4e33d29b5cb4ec67c64231815b8afbf  34-Harness操作台规格.md
01c278a0754d541c52a7cfa19513fc81be51a595d185d99468992ddfe8f4aad4  35-自主渗透与证据闭环规格.md
053a7281e024906fb04282587f4b994bb8c675e52c37d73dc46c9d81b10acd50  36-插件市场与能力发现规格.md
aaa0365f17ee0924f17eb2ed6afcdb4ac6728426c638cc5a3e705208d2ea2dbd  37-三样本逆向提取结果.md
e2af9163080c52c46753cbe7c6eab4e74e215bf93cf71d68a809dbbe8773e7ec  38-通用工作台规格.md
a351623dad68361f0c6b11cf3a8ea2c17965c10289476a8a47f3308a91baf4aa  39-交付收尾与能力接驳规格.md
0dc2fd4b2d24faf298a9fe0401a009ccc565bd8c7cb3d47866e8e95f6e56ccde  40-验证方案与批次规划.md
3997842d8d4361b7a4f6befca5b3d23bb3f158eb09a431d5139edc2ac884bf4e  99-二期池.md
7cb7143f6f26087ea6f8081e08f3d5add6bb167730279cd3616155fbd5ade83a  README.md
9388bb9309746c84ec589b24183894a6674fdb9f1a7cef8df3be0ae42287211f  _gen_manifest.cjs
78ff83361bfb51ed2283d860c620d0c5c2e378d978deec29dc18f43782d1450b  _verify-manifest.cjs
b1fa93672fc4945b24af980708e441569122240f2f3f9b4c51e6d0e1e53c442a  check-noroute.cjs
470ea1d45420c31c4ae0122299ac5f4486f31ffcd61195203041f626b5494c6e  check-plan.cjs
37480026dc33d5668268f0b6c238ec0bb12be277f6b7623f33d216a3cfe81180  noroute.allowlist
a36899146f43f91ee1f55e80ed8e7e349c638954cbecd0ea08a4f1af46fcd8c7  schemas/capsule.schema.json
ac90d5b9d54b4b4b02aacf2261fb40e85505cd8b90949003f68da61113d33f39  schemas/dataset-manifest.schema.json
6fd62e7981d0b09699fc2dce241ff216bc2ba26c4b985e76ce75f7db777a3c89  schemas/finding.schema.json
3fef0db628c9173d17837caa1542322714f1dde7d402e9a279fe8f93675a798a  schemas/playbook.schema.json
60cb09f88a8407d000bdae47d117aee04c6e9508df678c0e7bedb04c06178cab  schemas/profile.schema.json
cf94747e7962eda1e6ca9d7cb239c175eb3974eb00bfa9e90517e06153d07bd0  schemas/routeset.schema.json
```
