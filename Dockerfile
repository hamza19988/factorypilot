FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app
COPY static ./static
COPY data/manuals ./data/manuals
COPY data/samples ./data/samples
COPY data/golden ./data/golden
COPY docs ./docs
COPY scripts ./scripts

RUN useradd -m -u 1000 factorypilot && mkdir -p /app/var && chown -R factorypilot /app
USER factorypilot

ENV DB_PATH=/app/var/factorypilot.db PORT=7860
EXPOSE 7860
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request,os;urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",\"7860\")}/health')" || exit 1
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
