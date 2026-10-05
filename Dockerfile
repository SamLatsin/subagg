FROM python:3.12-slim

ARG MIHOMO_VERSION=v1.19.31
ARG TARGETARCH=amd64

RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl gzip tzdata \
 && rm -rf /var/lib/apt/lists/*

# Ядро mihomo используется как движок проверки нод.
# Если GitHub недоступен при сборке - положи бинарник рядом с Dockerfile
# под именем mihomo и собери с --build-arg MIHOMO_LOCAL=1
ARG MIHOMO_LOCAL=0
# requirements.txt в списке только чтобы COPY не падал, когда файла mihomo нет
COPY requirements.txt mihomo* /tmp/
RUN set -eux; \
    if [ "$MIHOMO_LOCAL" = "1" ] && [ -f /tmp/mihomo ]; then \
        install -m 0755 /tmp/mihomo /usr/local/bin/mihomo; \
    else \
        curl -fsSL -o /tmp/mihomo.gz \
          "https://github.com/MetaCubeX/mihomo/releases/download/${MIHOMO_VERSION}/mihomo-linux-${TARGETARCH}-${MIHOMO_VERSION}.gz"; \
        gunzip -c /tmp/mihomo.gz > /usr/local/bin/mihomo; \
        chmod 0755 /usr/local/bin/mihomo; \
    fi; \
    rm -f /tmp/mihomo.gz; \
    /usr/local/bin/mihomo -v

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

ENV SUBAGG_DATA_DIR=/data \
    SUBAGG_MIHOMO_BIN=/usr/local/bin/mihomo \
    PYTHONUNBUFFERED=1

VOLUME ["/data"]
EXPOSE 8080

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", \
     "--proxy-headers", "--forwarded-allow-ips", "*"]
