FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY seed ./seed
COPY alembic.ini .
COPY migrations ./migrations
# Расширение браузера из соседней папки репозитория (docker-compose: additional_contexts) —
# панель отдаёт его ZIP-архивом, всегда той же версии, что и задеплоенный код.
COPY --from=extension . ./extension

RUN useradd --system --uid 10001 roller \
    && mkdir -p /app/data /data \
    && chown roller /app/data /data
USER roller

EXPOSE 8200
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8200", "--proxy-headers", "--forwarded-allow-ips", "*", "--timeout-graceful-shutdown", "5"]
