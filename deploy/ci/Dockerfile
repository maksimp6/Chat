# syntax=docker/dockerfile:1
FROM python:3.14.7-slim

ARG NODE_VERSION=22.22.2

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_INPUT=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl git jq \
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends "nodejs=${NODE_VERSION}-1nodesource1" \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/alice-ci

COPY requirements.txt requirements-dev.txt ./
RUN --mount=type=cache,target=/root/.cache/pip pip install -r requirements.txt -r requirements-dev.txt

COPY package.json ./
RUN --mount=type=cache,target=/root/.npm npm install --ignore-scripts --no-audit --no-fund --package-lock=false

ENV PATH="/opt/alice-ci/node_modules/.bin:${PATH}"

CMD ["python", "--version"]
