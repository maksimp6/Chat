FROM node:22-bookworm-slim
ENV NODE_ENV=production
ENV PUPPETEER_SKIP_DOWNLOAD=true
RUN apt-get update && apt-get install -y --no-install-recommends \
      bash ca-certificates git openssh-client ripgrep python3 \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /workspace /home/node/.desktop-commander-device /home/node/.claude-server-commander \
    && chown -R node:node /workspace /home/node

COPY package.json package-lock.json dependency-smoke.cjs /opt/desktop-commander/
RUN cd /opt/desktop-commander \
    && npm ci --omit=dev --ignore-scripts --no-audit --no-fund \
    && node node_modules/@wonderwhy-er/desktop-commander/dist/index.js remote --help \
    && node dependency-smoke.cjs
COPY config.json /opt/desktop-commander/config.example.json
COPY entrypoint.sh /usr/local/bin/alice-desktop-commander
USER node
WORKDIR /workspace
ENTRYPOINT ["bash", "/usr/local/bin/alice-desktop-commander"]
CMD ["remote", "--disable-no-sleep"]
