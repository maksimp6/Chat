# Android build image for the Gradle-free debug APK (android/scripts/build_direct.sh).
# Every external input is pinned: the base image by digest, and every download by
# SHA-256 (checked with sha256sum -c at build time). Nothing is taken from "latest".

FROM eclipse-temurin:25-jdk@sha256:63f965b560b905c9633218330ac3516304b526a74fdc323a504b05c06e712c30

ARG ANDROID_BUILD_TOOLS_SHA256=bd3a4966912eb8b30ed0d00b0cda6b6543b949d5ffe00bea54c04c81e1561d88
ARG ANDROID_PLATFORM_SHA256=0988cacad01b38a18a47bac14a0695f246bc76c1b06c0eeb8eb0dc825ab0c8e0
ARG CHAQUOPY_ARM64_SHA256=13e1b17b2bd52983e13bf830a47317109f51a00ed23ecd91e8bc7d007c3f9033
ARG CHAQUOPY_STDLIB_SHA256=ee75692740405f62f4d0ca54e3838d17cf663b76d7f617edaf392d7f0afe1faf
ARG CHAQUOPY_STDLIB_PYC_SHA256=5d31a87468ad6d7db6783e34b7857dcebb7405cf4b33dfeac077e2f9ad20e9f3

ENV ANDROID_HOME=/opt/android-sdk \
    ANDROID_SDK_ROOT=/opt/android-sdk \
    CHAQUOPY_RUNTIME_DIR=/opt/chaquopy/3.14.8-0
ENV PATH=/opt/android-sdk/build-tools/35.0.0:${PATH}

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl unzip \
    && rm -rf /var/lib/apt/lists/*

# Android build-tools 35.0.0 (aapt2, d8, zipalign, apksigner) and platform android-35 (android.jar).
RUN set -eux; \
    mkdir -p /tmp/sdk "${ANDROID_HOME}/build-tools" "${ANDROID_HOME}/platforms"; \
    curl -fsSL --retry 5 --retry-delay 20 --retry-all-errors -o /tmp/sdk/build-tools.zip \
        https://dl.google.com/android/repository/build-tools_r35_linux.zip; \
    echo "${ANDROID_BUILD_TOOLS_SHA256}  /tmp/sdk/build-tools.zip" | sha256sum -c -; \
    unzip -q /tmp/sdk/build-tools.zip -d /tmp/sdk; \
    mv /tmp/sdk/android-15 "${ANDROID_HOME}/build-tools/35.0.0"; \
    curl -fsSL --retry 5 --retry-delay 20 --retry-all-errors -o /tmp/sdk/platform.zip \
        https://dl.google.com/android/repository/platform-35_r02.zip; \
    echo "${ANDROID_PLATFORM_SHA256}  /tmp/sdk/platform.zip" | sha256sum -c -; \
    unzip -q /tmp/sdk/platform.zip -d "${ANDROID_HOME}/platforms"; \
    rm -rf /tmp/sdk

# Chaquopy Python 3.14.8-0 runtime for Android (Maven Central com.chaquo.python:target).
RUN set -eux; \
    mkdir -p "${CHAQUOPY_RUNTIME_DIR}"; \
    base="https://repo1.maven.org/maven2/com/chaquo/python/target/3.14.8-0"; \
    fetch() { \
        curl -fsSL --retry 5 --retry-delay 20 --retry-all-errors \
            -o "${CHAQUOPY_RUNTIME_DIR}/$1" "$base/$1"; \
        echo "$2  ${CHAQUOPY_RUNTIME_DIR}/$1" | sha256sum -c -; \
    }; \
    fetch target-3.14.8-0-arm64-v8a.zip "${CHAQUOPY_ARM64_SHA256}"; \
    fetch target-3.14.8-0-stdlib.zip "${CHAQUOPY_STDLIB_SHA256}"; \
    fetch target-3.14.8-0-stdlib-pyc.zip "${CHAQUOPY_STDLIB_PYC_SHA256}"

# Smoke test: every tool the build script calls must exist and run.
RUN set -eux; \
    test -f "${ANDROID_HOME}/platforms/android-35/android.jar"; \
    command -v aapt2 d8 zipalign apksigner javac >/dev/null; \
    aapt2 version; \
    d8 --version; \
    apksigner --version; \
    javac -version

WORKDIR /workspace
