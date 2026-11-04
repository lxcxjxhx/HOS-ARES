# B45 执行沙箱镜像（最小面）。
#
# 纪律：本文件与 sandbox/ 下任何脚本都**不含地址**。
# 目标 host 由运行时从 RouteBinding 解出后经环境变量注入（H02 §2），
# 检查命令：python -c "from veripen_core.sandbox import scan_for_addresses as s; print(s('sandbox'))"
FROM python:3.13-slim

LABEL org.opencontainers.image.title="veripen-sandbox"
LABEL org.opencontainers.image.description="restricted executor for act.* actions"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/tmp

WORKDIR /work
RUN mkdir -p /work /out && chmod 0777 /out
COPY bin/ /opt/veripen/bin/
RUN chmod 0755 /opt/veripen/bin/* 2>/dev/null || true
ENV PATH="/opt/veripen/bin:${PATH}"

USER 65534:65534
ENTRYPOINT ["/usr/local/bin/python", "-I", "-B"]
