FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOST=0.0.0.0 \
    PORT=8080

WORKDIR /app

COPY requirements.txt requirements-postgres.txt ./
RUN apt-get update \
    && apt-get install -y --no-install-recommends adduser openssh-client coreutils \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir -r requirements.txt -r requirements-postgres.txt

RUN addgroup --system --gid 10001 alice \
    && adduser --system --uid 10001 --gid 10001 --home /home/alice --no-create-home alice \
    && mkdir -p /home/alice /app/data /app/logs

COPY . .

RUN chown -R alice:alice /home/alice /app/data /app/logs

ENV HOME=/home/alice
USER alice

EXPOSE 8080

CMD ["python", "app.py"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=3).read()"
