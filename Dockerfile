FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /srv

COPY pyproject.toml ./
RUN pip install --no-cache-dir pydantic PyYAML fastapi uvicorn

COPY veripen_core ./veripen_core
COPY api ./api
COPY config ./config

RUN mkdir -p /srv/out

ENTRYPOINT ["python", "-m", "uvicorn"]
