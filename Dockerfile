FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# 数据目录（与 docker-compose 的 volume 对应）
RUN mkdir -p /app/data

CMD ["python", "-m", "app.cli", "daemon"]
