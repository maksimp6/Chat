FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOST=0.0.0.0 \
    PORT=8080

WORKDIR /app

COPY requirements.txt requirements-redis.txt ./
RUN apt-get update \
    && apt-get install -y --no-install-recommends openssh-client coreutils \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir -r requirements.txt -r requirements-redis.txt

COPY . .

EXPOSE 8080

# Web process. Background jobs run in a separate worker from the same image:
#   python -m tasks          (long-running worker)
#   python -m tasks --drain  (Container Apps Job: exit when the queue is empty)
# Set ALICE_TASK_WORKER=external on the web container when a worker runs.
CMD ["python", "app.py"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=3).read()"
