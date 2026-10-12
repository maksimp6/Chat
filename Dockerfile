FROM python:3.14-slim AS tunnel-client

# OpenAI Secure MCP Tunnel runtime (openai/tunnel-client), pinned and checksum-verified.
# It only runs when the OPENAI_TUNNEL_* settings are present (scripts/container_entrypoint.py).
ARG TUNNEL_CLIENT_ZIP=tunnel-client-runtime-v0.0.14-linux-amd64.zip
ARG TUNNEL_CLIENT_SHA256=29d29cf860ada54e4d3c82c715f4fbfcff2abcdc2584c0fc26431308dfa2505b
RUN python -c "import sys, urllib.request; urllib.request.urlretrieve(sys.argv[1], '/tmp/tc.zip')" \
        "https://github.com/openai/tunnel-client/releases/download/v0.0.14/${TUNNEL_CLIENT_ZIP}" \
    && echo "${TUNNEL_CLIENT_SHA256}  /tmp/tc.zip" | sha256sum -c - \
    && python -m zipfile -e /tmp/tc.zip /tmp/tc \
    && install -m 0755 /tmp/tc/tunnel-client-runtime /usr/local/bin/tunnel-client-runtime

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
COPY --from=tunnel-client /usr/local/bin/tunnel-client-runtime /usr/local/bin/tunnel-client-runtime

RUN chown -R alice:alice /home/alice /app/data /app/logs

ENV HOME=/home/alice
USER alice

EXPOSE 8080

CMD ["python", "scripts/container_entrypoint.py"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=3).read()"
