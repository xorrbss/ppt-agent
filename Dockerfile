# syntax=docker/dockerfile:1.7

FROM python:3.11-slim-trixie AS fastapi-builder

WORKDIR /app/servers/fastapi

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

RUN python -m venv --without-pip /opt/venv \
    && pip install --no-cache-dir uv

COPY servers/fastapi/pyproject.toml servers/fastapi/uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv export --frozen --no-dev --no-emit-project -o /tmp/requirements.txt \
    && uv pip install --python /opt/venv/bin/python -r /tmp/requirements.txt

COPY servers/fastapi /app/servers/fastapi
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --python /opt/venv/bin/python --no-deps .
# mem0/spaCy BM25 lemmatization loads en_core_web_sm at runtime; spaCy tries pip to
# download it otherwise. Runtime image has no pip in PATH (--without-pip venv).
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --python /opt/venv/bin/python \
    "https://github.com/explosion/spacy-models/releases/download/en_core_web_sm-3.8.0/en_core_web_sm-3.8.0-py3-none-any.whl"
ENV HF_HOME=/root/.cache/huggingface \
    PRESENTON_FASTEMBED_ICON_CACHE_DIR=/root/.cache/presenton/fastembed-icons
# Warm FastEmbed caches into the image (not a BuildKit cache mount, or HF weights would be missing).
RUN /opt/venv/bin/python scripts/warm_fastembed_cache.py


FROM python:3.11-slim-trixie AS public-worker-runtime

ARG SOURCE_REVISION=unknown
ARG SOURCE_TREE_DIGEST=unknown
LABEL org.opencontainers.image.revision=${SOURCE_REVISION} \
      io.presenton.public-worker.source-tree-digest=${SOURCE_TREE_DIGEST} \
      org.opencontainers.image.title="Presenton public render/export worker"

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
      ca-certificates chromium chromium-sandbox fonts-liberation fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 65532 presenton-worker \
    && useradd --uid 65532 --gid 65532 --no-create-home --home-dir /tmp presenton-worker

COPY --from=fastapi-builder /opt/venv /opt/venv
COPY --from=fastapi-builder /app/servers/fastapi /app/servers/fastapi

ENV HOME=/tmp \
    TMPDIR=/tmp \
    XDG_RUNTIME_DIR=/tmp/runtime \
    LANG=C.UTF-8 \
    PATH=/opt/venv/bin:/usr/bin:/bin \
    PUPPETEER_EXECUTABLE_PATH=/usr/bin/chromium \
    CHROME_PATH=/usr/bin/chromium \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

USER 65532:65532
WORKDIR /app/servers/fastapi
ENTRYPOINT ["/opt/venv/bin/python"]


FROM python:3.11-slim-trixie AS authored-renderer-builder

WORKDIR /app/servers/fastapi

# Keep the security boundary independent from the network authoring/provider
# dependency graph. Only the modules imported by the one-shot worker are copied.
RUN python -m venv --without-pip /opt/venv \
    && pip install --no-cache-dir uv==0.11.17
COPY contracts/authored-renderer/requirements.lock /tmp/authored-renderer-requirements.lock
RUN uv pip install --python /opt/venv/bin/python --require-hashes \
      -r /tmp/authored-renderer-requirements.lock


FROM python:3.11-slim-trixie AS authored-renderer-runtime

ARG SOURCE_REVISION=unknown
ARG SOURCE_TREE_DIGEST=unknown
ARG CONTRACT_DIGEST=unknown
LABEL org.opencontainers.image.revision=${SOURCE_REVISION} \
      io.presenton.authored-renderer.source-tree-digest=${SOURCE_TREE_DIGEST} \
      io.presenton.authored-renderer.contract-digest=${CONTRACT_DIGEST} \
      org.opencontainers.image.title="Presenton authored renderer worker"

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
      ca-certificates chromium chromium-sandbox fonts-liberation fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 65532 authored \
    && useradd --uid 65532 --gid 65532 --no-create-home --home-dir /tmp/home authored

COPY --from=authored-renderer-builder /opt/venv /opt/venv
COPY servers/fastapi/utils/__init__.py /app/servers/fastapi/utils/__init__.py
COPY servers/fastapi/utils/authored_renderer_worker.py /app/servers/fastapi/utils/authored_renderer_worker.py
COPY servers/fastapi/utils/renderer_isolation.py /app/servers/fastapi/utils/renderer_isolation.py
COPY servers/fastapi/utils/renderer_worker_policy.py /app/servers/fastapi/utils/renderer_worker_policy.py
COPY servers/fastapi/utils/renderer_static_policy.py /app/servers/fastapi/utils/renderer_static_policy.py
COPY servers/fastapi/utils/renderer_internal_capability.py /app/servers/fastapi/utils/renderer_internal_capability.py
COPY servers/fastapi/utils/renderer_markup_policy.py /app/servers/fastapi/utils/renderer_markup_policy.py
COPY servers/fastapi/utils/renderer_css_policy.py /app/servers/fastapi/utils/renderer_css_policy.py
COPY servers/fastapi/utils/renderer_frame_codec.py /app/servers/fastapi/utils/renderer_frame_codec.py
COPY servers/fastapi/utils/renderer_worker_response.py /app/servers/fastapi/utils/renderer_worker_response.py
COPY servers/fastapi/utils/authored_renderer_session.py /app/servers/fastapi/utils/authored_renderer_session.py
COPY servers/fastapi/utils/authored_dom_quality.py /app/servers/fastapi/utils/authored_dom_quality.py
COPY servers/fastapi/utils/authored_dom_categories.py /app/servers/fastapi/utils/authored_dom_categories.py
COPY servers/fastapi/utils/authored_dom_fit_magnitude.py /app/servers/fastapi/utils/authored_dom_fit_magnitude.py
COPY servers/fastapi/utils/authored_dom_footer_zone.py /app/servers/fastapi/utils/authored_dom_footer_zone.py
COPY servers/fastapi/utils/authored_fragmentation_gate.py /app/servers/fastapi/utils/authored_fragmentation_gate.py
COPY servers/fastapi/utils/authored_dom_tags.py /app/servers/fastapi/utils/authored_dom_tags.py
COPY servers/fastapi/utils/authored_dom_text_escape.py /app/servers/fastapi/utils/authored_dom_text_escape.py
COPY servers/fastapi/utils/authored_qid_namespace.py /app/servers/fastapi/utils/authored_qid_namespace.py
COPY servers/fastapi/utils/authored_metrics_cache_policy.py /app/servers/fastapi/utils/authored_metrics_cache_policy.py
COPY servers/fastapi/utils/render_cache_budget.py /app/servers/fastapi/utils/render_cache_budget.py
COPY servers/fastapi/utils/runtime_settings.py /app/servers/fastapi/utils/runtime_settings.py
COPY servers/fastapi/utils/get_env.py /app/servers/fastapi/utils/get_env.py
COPY servers/fastapi/utils/get_env_programme.py /app/servers/fastapi/utils/get_env_programme.py
COPY servers/fastapi/utils/db_utils.py /app/servers/fastapi/utils/db_utils.py
COPY servers/fastapi/utils/owner_context.py /app/servers/fastapi/utils/owner_context.py
COPY servers/fastapi/utils/datetime_utils.py /app/servers/fastapi/utils/datetime_utils.py
COPY servers/fastapi/utils/parsers.py /app/servers/fastapi/utils/parsers.py
COPY servers/fastapi/utils/slide_capture.py /app/servers/fastapi/utils/slide_capture.py
COPY servers/fastapi/utils/slide_capture_assets.py /app/servers/fastapi/utils/slide_capture_assets.py
COPY servers/fastapi/utils/slide_capture_batch.py /app/servers/fastapi/utils/slide_capture_batch.py
COPY servers/fastapi/utils/slide_capture_chrome_discovery.py /app/servers/fastapi/utils/slide_capture_chrome_discovery.py
COPY servers/fastapi/utils/slide_capture_deck_scope.py /app/servers/fastapi/utils/slide_capture_deck_scope.py
COPY servers/fastapi/utils/slide_capture_glyph_probe.py /app/servers/fastapi/utils/slide_capture_glyph_probe.py
COPY servers/fastapi/utils/slide_capture_hit_test.py /app/servers/fastapi/utils/slide_capture_hit_test.py
COPY servers/fastapi/utils/slide_capture_pipe.py /app/servers/fastapi/utils/slide_capture_pipe.py
COPY servers/fastapi/utils/slide_capture_pipe_render.py /app/servers/fastapi/utils/slide_capture_pipe_render.py
COPY servers/fastapi/utils/slide_capture_pixel_probe.py /app/servers/fastapi/utils/slide_capture_pixel_probe.py
COPY servers/fastapi/utils/slide_capture_polygon_paint.py /app/servers/fastapi/utils/slide_capture_polygon_paint.py
COPY servers/fastapi/utils/slide_capture_reusable_render.py /app/servers/fastapi/utils/slide_capture_reusable_render.py
COPY servers/fastapi/utils/slide_capture_rounded_paint.py /app/servers/fastapi/utils/slide_capture_rounded_paint.py
COPY servers/fastapi/utils/slide_capture_worker_client.py /app/servers/fastapi/utils/slide_capture_worker_client.py
COPY servers/fastapi/utils/authored_fit_edits.py /app/servers/fastapi/utils/authored_fit_edits.py
COPY servers/fastapi/utils/slide_capture_fit.py /app/servers/fastapi/utils/slide_capture_fit.py
COPY servers/fastapi/utils/slide_capture_fit_script.py /app/servers/fastapi/utils/slide_capture_fit_script.py
COPY servers/fastapi/utils/slide_capture_worker_lease.py /app/servers/fastapi/utils/slide_capture_worker_lease.py
COPY servers/fastapi/utils/slide_capture_worker_session.py /app/servers/fastapi/utils/slide_capture_worker_session.py
COPY servers/fastapi/services/__init__.py /app/servers/fastapi/services/__init__.py
COPY servers/fastapi/services/provider_credential_vault.py /app/servers/fastapi/services/provider_credential_vault.py
COPY servers/fastapi/services/provider_secure_runtime.py /app/servers/fastapi/services/provider_secure_runtime.py
COPY servers/fastapi/services/provider_settings_values.py /app/servers/fastapi/services/provider_settings_values.py
COPY servers/fastapi/services/authored_vnext/__init__.py /app/servers/fastapi/services/authored_vnext/__init__.py
COPY servers/fastapi/services/authored_vnext/capability_registry.py /app/servers/fastapi/services/authored_vnext/capability_registry.py
COPY servers/fastapi/models/__init__.py /app/servers/fastapi/models/__init__.py
COPY servers/fastapi/models/user_config.py /app/servers/fastapi/models/user_config.py
COPY servers/fastapi/models/sql/provider_credential_reference.py /app/servers/fastapi/models/sql/provider_credential_reference.py
COPY servers/fastapi/models/authored_vnext/__init__.py /app/servers/fastapi/models/authored_vnext/__init__.py
COPY servers/fastapi/models/authored_vnext/deck/__init__.py /app/servers/fastapi/models/authored_vnext/deck/__init__.py
COPY servers/fastapi/models/authored_vnext/deck/scene.py /app/servers/fastapi/models/authored_vnext/deck/scene.py
COPY servers/fastapi/models/authored_vnext/master/__init__.py /app/servers/fastapi/models/authored_vnext/master/__init__.py
COPY servers/fastapi/models/authored_vnext/master/base.py /app/servers/fastapi/models/authored_vnext/master/base.py
COPY servers/fastapi/models/authored_vnext/master/contract.py /app/servers/fastapi/models/authored_vnext/master/contract.py
COPY servers/fastapi/constants/__init__.py /app/servers/fastapi/constants/__init__.py
COPY servers/fastapi/constants/settings_registry.py /app/servers/fastapi/constants/settings_registry.py

ENV HOME=/tmp/home \
    XDG_RUNTIME_DIR=/tmp/runtime \
    LANG=C.UTF-8 \
    PATH=/opt/venv/bin:/usr/bin:/bin \
    CHROME_PATH=/usr/bin/chromium \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

USER 65532:65532
WORKDIR /app/servers/fastapi
ENTRYPOINT ["/opt/venv/bin/python", "-m", "utils.authored_renderer_worker"]


FROM node:20-bookworm-slim AS nextjs-builder

WORKDIR /app/servers/nextjs

# This is intentionally a build argument: NEXT_PUBLIC_* values are embedded in
# the standalone Next bundle. SlideSpec is on by default; deployment profiles
# can explicitly set the values to false or use the backend kill switch.
ARG NEXT_PUBLIC_CHAT_FIRST_WORKBENCH_ENABLED=false
ARG NEXT_PUBLIC_SLIDESPEC_REPAIR_CANARY_UI=true
ARG NEXT_PUBLIC_SLIDESPEC_REPAIR_ALL_SLIDES=true
ENV NEXT_TELEMETRY_DISABLED=1 \
    NEXT_PUBLIC_CHAT_FIRST_WORKBENCH_ENABLED=${NEXT_PUBLIC_CHAT_FIRST_WORKBENCH_ENABLED} \
    NEXT_PUBLIC_SLIDESPEC_REPAIR_CANARY_UI=${NEXT_PUBLIC_SLIDESPEC_REPAIR_CANARY_UI} \
    NEXT_PUBLIC_SLIDESPEC_REPAIR_ALL_SLIDES=${NEXT_PUBLIC_SLIDESPEC_REPAIR_ALL_SLIDES}

COPY servers/nextjs/package.json servers/nextjs/package-lock.json ./
RUN --mount=type=cache,target=/root/.npm \
    npm ci

COPY servers/nextjs /app/servers/nextjs
RUN npm run build \
    && rm -rf .next-build/cache


FROM node:20-bookworm-slim AS assets-builder

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates unzip \
    && rm -rf /var/lib/apt/lists/*

COPY package.json /app/

RUN mkdir -p /app/document-extraction-liteparse \
    && npm --prefix /app/document-extraction-liteparse init -y \
    && npm --prefix /app/document-extraction-liteparse install @llamaindex/liteparse@1.4.0 --omit=dev

COPY resources/document-extraction/liteparse_runner.mjs /app/document-extraction-liteparse/liteparse_runner.mjs
COPY scripts/sync-presentation-export.cjs /app/scripts/sync-presentation-export.cjs
COPY scripts/lib/presentation-export-readiness.cjs /app/scripts/lib/presentation-export-readiness.cjs
# The sync requires this module (sync-presentation-export.cjs:29) and reads the
# checksum manifest from <repoRoot>/compatibility (:46). Both were missing, so
# `docker build --target assets-builder` failed on the RUN below.
COPY scripts/lib/presentation-export-assets.cjs /app/scripts/lib/presentation-export-assets.cjs
COPY compatibility/upstream-compatibility.json /app/compatibility/upstream-compatibility.json
# The sync installs the matching sharp native addon before recording its version marker.
RUN rm -rf /app/presentation-export \
    && node /app/scripts/sync-presentation-export.cjs --force \
    && chmod +x /app/presentation-export/py/convert-linux-x64


FROM python:3.11-slim-trixie AS runtime

WORKDIR /app

ARG INSTALL_TESSERACT=true
ARG INSTALL_LIBREOFFICE=true

# LiteParse uses Node + @llamaindex/liteparse (same runner as Electron); OCR uses Tesseract.
ENV APP_DATA_DIRECTORY=/app_data \
    TEMP_DIRECTORY=/tmp/presenton \
    EXPORT_PACKAGE_ROOT=/app/presentation-export \
    EXPORT_RUNTIME_DIR=/app/presentation-export \
    BUILT_PYTHON_MODULE_PATH=/app/presentation-export/py/convert-linux-x64 \
    PRESENTON_APP_ROOT=/app \
    AUTHORED_OCR_TESSDATA_PATH=/app/tessdata \
    HF_HOME=/root/.cache/huggingface \
    PRESENTON_FASTEMBED_ICON_CACHE_DIR=/root/.cache/presenton/fastembed-icons \
    PATH="/opt/venv/bin:${PATH}" \
    NODE_ENV=production \
    START_OLLAMA=false \
    PUPPETEER_EXECUTABLE_PATH=/usr/bin/chromium

RUN set -eux; \
    packages="ca-certificates curl nginx fontconfig imagemagick zstd chromium \
      fonts-liberation fonts-noto-cjk xdg-utils \
      libasound2t64 libatk-bridge2.0-0t64 libatk1.0-0t64 libatspi2.0-0t64 \
      libcairo2 libcups2t64 libdbus-1-3 libdrm2 libexpat1 libgbm1 \
      libglib2.0-0t64 libgtk-3-0t64 libnspr4 libnss3 libpango-1.0-0 \
      libx11-6 libxcb1 libxcomposite1 libxdamage1 libxext6 libxfixes3 \
      libxkbcommon0 libxrandr2 libxshmfence1 libxss1 libxtst6"; \
    if [ "$INSTALL_LIBREOFFICE" = "true" ]; then packages="$packages libreoffice"; fi; \
    if [ "$INSTALL_TESSERACT" = "true" ]; then packages="$packages tesseract-ocr tesseract-ocr-eng"; fi; \
    apt-get update; \
    apt-get install -y --no-install-recommends $packages; \
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash -; \
    apt-get install -y --no-install-recommends nodejs; \
    rm -rf /var/lib/apt/lists/*

RUN mkdir -p /app/scripts /app/servers/fastapi /app/servers/nextjs
RUN mkdir -p /app_data/exports /app_data/images /app_data/uploads /app_data/fonts /app_data/pptx-to-html /app_data-private/template-v2-imports /app_data-private/authored-exact-sources \
    && chmod -R a+rX /app_data \
    && chmod 0700 /app_data-private /app_data-private/template-v2-imports /app_data-private/authored-exact-sources

COPY --from=fastapi-builder /opt/venv /opt/venv
COPY --from=fastapi-builder /app/servers/fastapi /app/servers/fastapi
COPY --from=fastapi-builder /root/.cache/huggingface /root/.cache/huggingface
COPY --from=fastapi-builder /root/.cache/presenton/fastembed-icons /root/.cache/presenton/fastembed-icons

COPY --from=assets-builder /app/package.json /app/package.json
COPY --from=assets-builder /app/document-extraction-liteparse /app/document-extraction-liteparse
COPY --from=assets-builder /app/presentation-export /app/presentation-export
COPY --from=assets-builder /app/scripts/sync-presentation-export.cjs /app/scripts/sync-presentation-export.cjs
COPY --from=assets-builder /app/scripts/lib/presentation-export-readiness.cjs /app/scripts/lib/presentation-export-readiness.cjs

# Bundle Tesseract language data so OCR (LiteParse/tesseract.js) works offline.
# Resolved by LiteParseService via PRESENTON_APP_ROOT (=/app) -> /app/tessdata.
COPY tessdata /app/tessdata

COPY --from=nextjs-builder /app/servers/nextjs/.next-build/standalone/ /app/servers/nextjs/
COPY --from=nextjs-builder /app/servers/nextjs/public /app/servers/nextjs/public
COPY --from=nextjs-builder /app/servers/nextjs/.next-build/static /app/servers/nextjs/.next-build/static

COPY start.js LICENSE NOTICE ./
COPY scripts/presenton-terminal-banner.mjs /app/scripts/presenton-terminal-banner.mjs
COPY nginx.conf /etc/nginx/nginx.conf

EXPOSE 80
CMD ["node", "/app/start.js"]
