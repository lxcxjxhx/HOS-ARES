VeriPen 离线包 —— 三步安装
============================

一、装什么
    本包在**断网环境**内完成部署。预装要求只有两项，且都不联网：
      · docker（>= 24）
      · Python 3.11+（运行时本体；依赖 wheel 随包放在 wheels/，用 --no-index 装）
    不联网、不拉镜像、不改系统包。

二、怎么装
    1) 拷贝整个目录到目标机
    2) sh install.sh
    3) python -m veripen_cli.cmd_doctor       # 20 项自检，红了先别跑

    校验包完整性（推荐先单独跑一遍）：
       sha256sum -c SHA256SUMS

三、出问题找谁
    - 自检红项：把 doctor 输出整段贴过来，它按项写清了"期望什么/实际什么/怎么修"
    - 包不完整：先跑 sha256sum -c SHA256SUMS，把失败的那几行贴过来
    - 回滚：保留上一版目录，sh install.sh --rollback 一步切回

四、包里有什么
    install.sh / uninstall.sh   安装与卸载（卸载保留数据）
    SHA256SUMS                  全部文件的校验和
    manifest.json               清单（含版本、字节数、备注）
    wheels/                     运行期依赖 *.whl + requirements.lock（--no-index）
    profiles/                   客户档样例（不含真实凭据）
    images/                     镜像 tar（需在有 docker 的构建机生成）

五、已知限制（如实写明，不藏）
    - 本包在无 docker 的环境构建，images/ 可能为空；
      此时 doctor 与离线包校验都会报出来，**不会**当通过。
    - 安装脚本不含任何外网拉取；若你在里面看到 curl/wget/pip install，
      那是包被改过，请重新索取。
