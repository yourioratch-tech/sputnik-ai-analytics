FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN groupadd --system sputnik && useradd --system --gid sputnik --home /app sputnik
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
COPY configs ./configs
RUN pip install --upgrade pip && pip install .

RUN mkdir -p /data /app/reports/jobs && chown -R sputnik:sputnik /data /app/reports
USER sputnik

EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/health', timeout=3)"

CMD ["uvicorn", "sputnik.api:app", "--host", "0.0.0.0", "--port", "8765", "--proxy-headers", "--forwarded-allow-ips", "*"]
