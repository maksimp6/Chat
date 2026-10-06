FROM node:22-bookworm-slim
ENV NODE_ENV=production
ENV PUPPETEER_SKIP_DOWNLOAD=true
RUN apt-get -o Acquire::Retries=0 -o Acquire::http::Timeout=30 update \
    && apt-get -o Acquire::Retries=0 -o Acquire::http::Timeout=30 install -y --no-install-recommends \
      bash ca-certificates git openssh-client ripgrep python3 python3-pip python3-venv chromium fonts-liberation \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /workspace /rdc-state /home/node/.desktop-commander-device /home/node/.claude-server-commander \
    && chown -R node:node /workspace /rdc-state /home/node

COPY package.json package-lock.json dependency-smoke.cjs /opt/desktop-commander/
RUN cd /opt/desktop-commander \
    && npm ci --omit=dev --ignore-scripts --no-audit --no-fund \
    && node node_modules/@wonderwhy-er/desktop-commander/dist/index.js remote --help \
    && node dependency-smoke.cjs
COPY config.json /opt/desktop-commander/config.example.json
COPY entrypoint.sh /usr/local/bin/alice-desktop-commander
COPY dev-bootstrap.sh /usr/local/bin/alice-rdc-dev-bootstrap
RUN chmod 0755 /usr/local/bin/alice-rdc-dev-bootstrap
COPY pairing-handoff.cjs /opt/desktop-commander/pairing-handoff.cjs
COPY desktop-session.cjs browser-smoke.cjs /opt/desktop-commander/
COPY state-store.py /opt/desktop-commander/state-store.py
USER node
WORKDIR /workspace
ENTRYPOINT ["bash", "/usr/local/bin/alice-desktop-commander"]
CMD ["remote", "--disable-no-sleep"]
