FROM node:22-bookworm-slim

ARG CHROME_VERSION=154.0.8037.97-1

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl gnupg xvfb x11vnc novnc websockify \
    && curl -fsSL https://dl.google.com/linux/linux_signing_key.pub \
       | gpg --dearmor -o /usr/share/keyrings/google-chrome.gpg \
    && echo "deb [arch=amd64 signed-by=/usr/share/keyrings/google-chrome.gpg] http://dl.google.com/linux/chrome/deb/ stable main" \
       > /etc/apt/sources.list.d/google-chrome.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends "google-chrome-stable=${CHROME_VERSION}" \
    && rm -rf /var/lib/apt/lists/*

# These public TLS roots are published by the Russian Ministry of Digital
# Development. Keeping reviewed copies in the image makes the build reproducible.
COPY certificates/*.crt /usr/local/share/ca-certificates/
RUN update-ca-certificates

WORKDIR /opt/chrome-worker
COPY package.json package-lock.json ./
RUN npm ci --omit=dev
COPY server.mjs mcp.mjs oauth.mjs state.mjs idp-auth.mjs jwt-verify.mjs takeover.mjs ./

RUN mkdir -p /state/profile && chown -R node:node /state /opt/chrome-worker
USER node
ENV PORT=8080 \
    CHROME_EXECUTABLE_PATH=/usr/bin/google-chrome-stable \
    CHROME_PROFILE_DIR=/state/profile \
    DISPLAY=:99 \
    BROWSER_TAKEOVER_ENABLED=1
EXPOSE 8080
CMD ["node", "server.mjs"]
