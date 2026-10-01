FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    WORLDPLAN_HOST=0.0.0.0 \
    WORLDPLAN_PORT=8765 \
    WORLDPLAN_LEDGER_ROOT=/data

WORKDIR /app
COPY pyproject.toml README.md ./
COPY worldplan ./worldplan
RUN pip install --no-cache-dir . \
 && useradd --system --uid 10001 worldplan \
 && mkdir -p /data && chown worldplan /data

USER worldplan
VOLUME ["/data"]
EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=5s \
  CMD python -c "import urllib.request,os;urllib.request.urlopen('http://127.0.0.1:'+os.environ['WORLDPLAN_PORT']+'/healthz',timeout=4)"
# 밖으로 열 때는 WORLDPLAN_TOKEN 을 세워라 (docker run -e WORLDPLAN_TOKEN=...)
CMD ["worldplan", "serve"]
