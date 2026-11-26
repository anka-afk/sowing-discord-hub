FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN groupadd --system collector \
    && useradd --system --gid collector --home-dir /app --no-create-home collector

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY sowing_collector ./sowing_collector
COPY main.py config.example.yaml ./

RUN mkdir -p /app/data \
    && chown -R collector:collector /app/data

USER collector

CMD ["python", "main.py", "--config", "/app/config.yaml"]
