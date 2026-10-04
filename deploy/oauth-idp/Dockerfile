FROM node:22-bookworm-slim

# No dependencies: only Node's built-in crypto and HTTP, so the image builds in seconds.
WORKDIR /opt/oauth-idp
COPY package.json crypto.mjs jwt-sign.mjs jwt-verify.mjs idp.mjs server.mjs ./
RUN chown -R node:node /opt/oauth-idp
USER node
ENV PORT=8080
EXPOSE 8080
CMD ["node", "server.mjs"]
