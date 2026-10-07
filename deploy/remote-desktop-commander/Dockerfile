# syntax=docker/dockerfile:1.7
FROM node:22-bookworm-slim
ENV NODE_ENV=production
ENV PUPPETEER_SKIP_DOWNLOAD=true
RUN apt-get -o Acquire::Retries=0 -o Acquire::http::Timeout=30 update \
    && apt-get -o Acquire::Retries=0 -o Acquire::http::Timeout=30 install -y --no-install-recommends git openssh-client python3 \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /workspace /rdc-state /home/node/.desktop-commander-device /home/node/.claude-server-commander \
    && chown -R node:node /workspace /rdc-state /home/node
COPY package.json package-lock.json dependency-smoke.cjs /opt/desktop-commander/
RUN cd /opt/desktop-commander \
    && npm ci --omit=dev --ignore-scripts --no-audit --no-fund \
    && node node_modules/@wonderwhy-er/desktop-commander/dist/index.js remote --help \
    && node dependency-smoke.cjs
COPY --link config.json /opt/desktop-commander/config.example.json
COPY --link --chmod=0755 entrypoint.sh /usr/local/bin/alice-desktop-commander
COPY --link --chmod=0755 dev-bootstrap.sh /usr/local/bin/alice-rdc-dev-bootstrap
COPY --link pairing-handoff.cjs /opt/desktop-commander/pairing-handoff.cjs
COPY --link desktop-session.cjs browser-smoke.cjs mcp-gateway.mjs stdio-server.mjs /opt/desktop-commander/
COPY --link state-store.py /opt/desktop-commander/state-store.py
COPY --link github_known_hosts /opt/desktop-commander/github_known_hosts
USER node
WORKDIR /workspace
ENTRYPOINT ["bash", "/usr/local/bin/alice-desktop-commander"]
CMD ["remote", "--disable-no-sleep"]
