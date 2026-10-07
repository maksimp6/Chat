ARG RDC_BASE_IMAGE
FROM ${RDC_BASE_IMAGE}
ENV NODE_ENV=production
ENV PUPPETEER_SKIP_DOWNLOAD=true
COPY config.json /opt/desktop-commander/config.example.json
COPY entrypoint.sh /usr/local/bin/alice-desktop-commander
COPY dev-bootstrap.sh /usr/local/bin/alice-rdc-dev-bootstrap
RUN chmod 0755 /usr/local/bin/alice-rdc-dev-bootstrap
COPY pairing-handoff.cjs /opt/desktop-commander/pairing-handoff.cjs
COPY desktop-session.cjs browser-smoke.cjs mcp-gateway.mjs stdio-server.mjs /opt/desktop-commander/
COPY state-store.py /opt/desktop-commander/state-store.py
USER node
WORKDIR /workspace
ENTRYPOINT ["bash", "/usr/local/bin/alice-desktop-commander"]
CMD ["remote", "--disable-no-sleep"]
