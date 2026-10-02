FROM python:3.12-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends chromium chromium-driver \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 10001 --create-home browser
WORKDIR /app
COPY browser /app/browser
RUN mkdir /data && chown browser:browser /data
USER 10001:10001
ENV PYTHONUNBUFFERED=1
ENTRYPOINT ["python", "-m", "browser.server_worker", "serve"]
