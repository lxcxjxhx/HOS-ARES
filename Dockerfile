FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /srv

COPY pyproject.toml requirements.lock ./
# 依赖一律从**锁**装（B203）：手写清单在这里漏过 jsonschema，镜像里一加载
# 演练剧本就 ModuleNotFoundError。锁里带 dev 的 pytest，镜像多几 MB，换"只有一处真相"。
RUN pip install --no-cache-dir -r requirements.lock

COPY veripen_core ./veripen_core
COPY api ./api
COPY config ./config

RUN mkdir -p /srv/out

ENTRYPOINT ["python", "-m", "uvicorn"]
