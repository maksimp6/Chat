FROM node:22-bookworm-slim
ARG DC_VERSION=0.2.52
ENV NODE_ENV=production
ENV PUPPETEER_SKIP_DOWNLOAD=true
RUN apt-get update && apt-get install -y --no-install-recommends \
      bash ca-certificates git openssh-client ripgrep python3 \
    && rm -rf /var/lib/apt/lists/* \
    && npm install --prefix /opt/desktop-commander --omit=dev --ignore-scripts \
      --no-audit --no-fund @wonderwhy-er/desktop-commander@${DC_VERSION} \
    && mkdir -p /workspace /home/node/.desktop-commander-device /home/node/.claude-server-commander \
    && chown -R node:node /workspace /home/node \
    && node /opt/desktop-commander/node_modules/@wonderwhy-er/desktop-commander/dist/index.js remote --help
COPY config.json /opt/desktop-commander/config.example.json
COPY entrypoint.sh /usr/local/bin/alice-desktop-commander
USER node
WORKDIR /workspace
ENTRYPOINT ["bash", "/usr/local/bin/alice-desktop-commander"]
CMD ["remote", "--disable-no-sleep"]
