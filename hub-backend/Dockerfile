FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libwebp7 \
    zlib1g \
    libjpeg62-turbo \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system sowinghub \
    && useradd --system --gid sowinghub --home-dir /app --no-create-home sowinghub

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

RUN mkdir -p /data/media/private /data/media/public \
    && chown -R sowinghub:sowinghub /app /data

USER sowinghub

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--proxy-headers", "--forwarded-allow-ips=127.0.0.1,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16"]
