FROM node:24-bookworm-slim

ARG SOURCE_REVISION=unknown
LABEL org.opencontainers.image.revision=${SOURCE_REVISION} \
      org.opencontainers.image.title="Presenton authored renderer security harness"

RUN apt-get update \
    && apt-get install -y --no-install-recommends chromium chromium-sandbox ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 65532 authored \
    && useradd --uid 65532 --gid 65532 --no-create-home --home-dir /tmp/home authored \
    && mkdir -p /opt/harness/corpus /opt/harness/policy

COPY corpus/ /opt/harness/corpus/
COPY policy/ /opt/harness/policy/
COPY harness/runtime.mjs /opt/harness/runtime.mjs

ENV HOME=/tmp/home \
    XDG_RUNTIME_DIR=/tmp/runtime \
    CHROME_PATH=/usr/bin/chromium \
    NODE_OPTIONS=--disable-warning=ExperimentalWarning

USER 65532:65532
WORKDIR /opt/harness
ENTRYPOINT ["node", "/opt/harness/runtime.mjs"]
