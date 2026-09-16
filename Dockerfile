FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SITE_TRIAL_HOST=0.0.0.0 \
    SITE_TRIAL_PORT=8080 \
    SITE_TRIAL_PROJECT_ROOT=/app \
    SITE_TRIAL_DATA_ROOT=/data

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
COPY data/cancer_aliases.json data/who-mcp-china-latest-200.json ./data/
COPY skills ./skills
COPY web ./web

RUN pip install --no-cache-dir . \
    && useradd --create-home --uid 10001 matcher \
    && mkdir -p /data \
    && chown -R matcher:matcher /data

USER matcher
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=3)"

CMD ["uvicorn", "china_trial_demo.web:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]
