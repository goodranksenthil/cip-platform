FROM python:3.12-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY tests ./tests
# Run as a non-root user
RUN useradd --create-home --uid 10001 cip && mkdir -p /app/storage /data/blob && chown -R cip /app/storage /data/blob
USER cip
EXPOSE 8000
# Render (and most PaaS hosts) inject $PORT; docker compose / local default to 8000.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'"]
