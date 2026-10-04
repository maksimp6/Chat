FROM python:3.14-slim

ARG NODE_MAJOR=22

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_INPUT=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl git \
    && curl -fsSL https://deb.nodesource.com/setup_${NODE_MAJOR}.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/alice-ci

COPY requirements.txt requirements-dev.txt ./
RUN pip install -r requirements.txt -r requirements-dev.txt

COPY package.json ./
RUN npm install --ignore-scripts --no-audit --no-fund --package-lock=false \
    && npm cache clean --force

ENV PATH="/opt/alice-ci/node_modules/.bin:${PATH}"

CMD ["python", "--version"]
