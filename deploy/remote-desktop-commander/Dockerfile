# syntax=docker/dockerfile:1.7
ARG RDC_BASE_IMAGE
FROM ${RDC_BASE_IMAGE}
ENV NODE_ENV=production
ENV PUPPETEER_SKIP_DOWNLOAD=true
COPY --link config.json /opt/desktop-commander/config.example.json
COPY --link --chmod=0755 entrypoint.sh /usr/local/bin/alice-desktop-commander
COPY --link --chmod=0755 dev-bootstrap.sh /usr/local/bin/alice-rdc-dev-bootstrap
COPY --link pairing-handoff.cjs /opt/desktop-commander/pairing-handoff.cjs
COPY --link desktop-session.cjs browser-smoke.cjs mcp-gateway.mjs stdio-server.mjs /opt/desktop-commander/
COPY --link state-store.py /opt/desktop-commander/state-store.py
USER node
WORKDIR /workspace
ENTRYPOINT ["bash", "/usr/local/bin/alice-desktop-commander"]
CMD ["remote", "--disable-no-sleep"]
