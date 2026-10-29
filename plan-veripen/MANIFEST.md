# MANIFEST · plan-veripen v2.2

> 复制后校验完整性：文件数、字节数、sha256 三项都对得上，才算拷对。
> **本文件不含自身哈希**（自指不可校验），除此之外全部文件都在表里。

| 项 | 值 |
|---|---|
| 包版本 | `plan-v2.2` |
| 生成时间 | 2026-10-29T07:12:26.355Z |
| 文件数 | 36 |
| 总字节 | 262244 |
| 总行数（文本） | 4976 |
| 内容 | 27 篇规格 + README + 2 个自检脚本 + 4 份 JSON Schema |

## 校验命令

```bash
node check-plan.cjs        # 结构 + 交叉引用 + schema 可解析
node _verify-manifest.cjs  # 逐个文件比对 sha256
```

## 文件清单

| 文件 | 字节 | 行 | sha256（前 16） |
|---|---:|---:|---|
| `00-总规划.md` | 11954 | 171 | `8292bc7e0d940439` |
| `01-包结构与内核契约.md` | 10864 | 210 | `2b878a2a62069ee7` |
| `02-客户档Profile规格.md` | 6696 | 156 | `f2d30f20882e5f14` |
| `03-数据模型与状态机.md` | 9501 | 214 | `5f16ed4c50bb81ae` |
| `04-调度规格.md` | 6476 | 129 | `acc85ea62a309181` |
| `05-安全边界规格.md` | 7561 | 142 | `e52f10271142bbb7` |
| `06-证据与replay规格.md` | 8808 | 207 | `8429057b96864ff2` |
| `07-Oracle规格.md` | 7847 | 120 | `2de78fa96909c536` |
| `08-Agent循环规格.md` | 7375 | 133 | `066c34eeae6dbc9f` |
| `09-报告与交付格式.md` | 6012 | 137 | `be974db704e61df4` |
| `10-评测协议.md` | 6216 | 127 | `e9ae71c5fc6f82d7` |
| `11-测试策略与靶场用例.md` | 10148 | 164 | `6cdcad432cdce8d8` |
| `12-工程与交付.md` | 8105 | 162 | `fdcfa57a21bb259d` |
| `13-风险登记册.md` | 6936 | 86 | `9c9ce15485280b33` |
| `14-二期路线.md` | 7291 | 136 | `9b0f00bab3faff3e` |
| `15-开工提示词.md` | 8169 | 169 | `1038e7d4587ca3fe` |
| `16-完整范围与交付地图.md` | 8025 | 124 | `ca76c16aa78260ac` |
| `17-权威链与证据门.md` | 11495 | 178 | `03ad1cec6ffd526b` |
| `18-Oracle目录（14类判定矩阵）.md` | 11447 | 182 | `9992a87380859200` |
| `19-接口契约与用例生成.md` | 5665 | 119 | `3d72e73d19442e5f` |
| `20-认证体系测试点.md` | 6403 | 133 | `ada02ecd05caf873` |
| `21-实现批次与Agent执行计划.md` | 13261 | 197 | `885cf90ff9f8fe4d` |
| `22-产品与人机协同.md` | 7853 | 135 | `30f0d181783e3641` |
| `23-可观测性与诊断.md` | 4104 | 93 | `e0ab464e53e40941` |
| `24-服务API与错误码.md` | 10885 | 203 | `e38f084bcb3f29bc` |
| `25-靶场与真实数据规格.md` | 10022 | 171 | `8d37e15253957ebe` |
| `26-数据真实性与来源政策.md` | 9632 | 201 | `6589ef174d345b0c` |
| `99-二期池.md` | 1922 | 21 | `34ddf586827acb43` |
| `README.md` | 11044 | 217 | `09ae8020660ab1ce` |
| `_gen_manifest.cjs` | 1972 | 65 | `f4a8217161bfc3bf` |
| `_verify-manifest.cjs` | 738 | 17 | `c018f719d48e7e94` |
| `check-plan.cjs` | 1524 | 50 | `ce66241f5dbe0359` |
| `schemas/capsule.schema.json` | 5652 | 155 | `5b8fbdbf03e19d24` |
| `schemas/dataset-manifest.schema.json` | 2229 | 52 | `b55274dd0724529e` |
| `schemas/finding.schema.json` | 1758 | 36 | `21dfa24b5bec9706` |
| `schemas/profile.schema.json` | 6654 | 164 | `53d6d46a1028b7c7` |

## 完整 sha256

```
8292bc7e0d94043909b4766a3f64dad33c9432db361accb8baa11af05530affb  00-总规划.md
2b878a2a62069ee7ba8a2de0ecb8a99f25a377070837161721e27bface983841  01-包结构与内核契约.md
f2d30f20882e5f14a0217288dc5536be207d4a754bdb8182615d807006960b3d  02-客户档Profile规格.md
5f16ed4c50bb81ae4f6e641e0b20d20c266c506f0dfac1111e6bc41f835ccf64  03-数据模型与状态机.md
acc85ea62a30918168babafeb1ec2e83a95e5e22a4fbfa41c9b2713f7d32b516  04-调度规格.md
e52f10271142bbb7df84dc03b6584d08e1cab474c6b5e71cb3edbbddfe943da2  05-安全边界规格.md
8429057b96864ff24cd20db0cd560bd16b946c7c71b800e5de39525c3177f074  06-证据与replay规格.md
2de78fa96909c536c9051df6c1c610c387e0452ee05d27edae3343f5ab6676fa  07-Oracle规格.md
066c34eeae6dbc9fc2aa8026a2d4cdf62e5fa19303686830981560d9124be94d  08-Agent循环规格.md
be974db704e61df470c87c9e195ac8fa12f2f6f08b482f13d7cf432b8f247d9b  09-报告与交付格式.md
e9ae71c5fc6f82d78f3cbe1c7bc55009d0c8ef61a50987f046150a2297d87f70  10-评测协议.md
6cdcad432cdce8d88c097f76070fdd66bb23a7d3674bcf7ed2522189aff87775  11-测试策略与靶场用例.md
fdcfa57a21bb259d70c703358ff7d3927706190b590532c3e14dcda9c0f6bf31  12-工程与交付.md
9c9ce15485280b33e847aa1aeafb67508022a53e49a08c76f823c9bef4bd78e5  13-风险登记册.md
9b0f00bab3faff3e3d0ab1e2e9ecd59fef03acf82b2fad9f8ba787ba7ae23289  14-二期路线.md
1038e7d4587ca3feab3e3bcf66e4a3b981de407d2e266e29d70b1f58aa6ef421  15-开工提示词.md
ca76c16aa78260ac7d0e28f93a5e93800cd4ba7da62b1f8373c401596ff143bd  16-完整范围与交付地图.md
03ad1cec6ffd526b8c63997cf47b0e09047d3ce01313d732cca1e989748616b1  17-权威链与证据门.md
9992a87380859200b328c8f58b79ef276973f06367f5fe5b83bb66b3123f486b  18-Oracle目录（14类判定矩阵）.md
3d72e73d19442e5f742ae39bbbe6d842d46f9a150639323d35cc6e6c71c73d06  19-接口契约与用例生成.md
ada02ecd05caf873a10698fc0b5cb371929cb2a8201698535ea15ff3b205113f  20-认证体系测试点.md
885cf90ff9f8fe4d87184b6b3c9a67c3ff1c48dc70ee171cf3be708784ce91e1  21-实现批次与Agent执行计划.md
30f0d181783e36418c0ca2fe32cb4c87e2c7931d05663b92af94b513bfe8cef6  22-产品与人机协同.md
e0ab464e53e4094131ec99b2fef1cc1044fb08fb6a89927c34a1a90b771d10a0  23-可观测性与诊断.md
e38f084bcb3f29bc2098d993bbe681e811e35a1be4fe5683e2744676e541eb81  24-服务API与错误码.md
8d37e15253957ebe6ac58cdf5d6c8f2c6d838f79b67ef16a5f9d10fd81fbea56  25-靶场与真实数据规格.md
6589ef174d345b0cc62d09be282819f98d5eb0ffb2695e6803090115cf1e2acb  26-数据真实性与来源政策.md
34ddf586827acb43fe056fef96dcfee8cfc9ea1b4d11af1b37508b131e6936d0  99-二期池.md
09ae8020660ab1cee3b525558a80ab3c76ef81f3d199343346ca28a3b090d7cd  README.md
f4a8217161bfc3bf78ebd8fbdf072a34d80d153be135b5058d232224b308a31a  _gen_manifest.cjs
c018f719d48e7e9432728394ddf5d9253a69feaf52cbf18cd24fe830fb9ae2ab  _verify-manifest.cjs
ce66241f5dbe0359a4fc5155771610a7fc86ca3b379128c6f1fb019fa9b09b69  check-plan.cjs
5b8fbdbf03e19d24e5225a3e5038b87952b2ac4cb806ab4709e60fcba7c4e93a  schemas/capsule.schema.json
b55274dd0724529e0b4136535e88671dc9d35e1056c6bf6e28d5f1429c8ea505  schemas/dataset-manifest.schema.json
21dfa24b5bec970619213285ec772bf0ff0391b48ae8adb13e34aaa52baf61bf  schemas/finding.schema.json
53d6d46a1028b7c710f05d185782a1d69fc45ec76001e44448ade2c80b10b523  schemas/profile.schema.json
```
