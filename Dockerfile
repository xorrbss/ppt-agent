# syntax=docker/dockerfile:1.4
FROM python:3.11-slim-bookworm

WORKDIR /app

# Docling + CPU torch: declared in pyproject.toml; lockfile uses PyTorch CPU index.
# UV_EXTRA_INDEX_URL mirrors the old `pip install docling --extra-index-url .../cpu`.
ENV APP_DATA_DIRECTORY=/app_data \
    TEMP_DIRECTORY=/tmp/presenton \
    PUPPETEER_EXECUTABLE_PATH=/usr/bin/chromium \
    UV_SYSTEM_PYTHON=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_EXTRA_INDEX_URL=https://download.pytorch.org/whl/cpu \
    PATH="/root/.local/bin:${PATH}"

RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates curl \
      nginx libreoffice fontconfig chromium imagemagick zstd \
    && curl -LsSf https://astral.sh/uv/install.sh | sh \
    && rm -rf /var/lib/apt/lists/*

RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

RUN curl -fsSL https://ollama.com/install.sh | sh

COPY servers/fastapi /app/servers/fastapi
WORKDIR /app/servers/fastapi
RUN --mount=type=cache,target=/root/.cache/uv \
    uv export --frozen --no-dev --no-emit-project -o /tmp/requirements.txt \
    && uv pip install --system -r /tmp/requirements.txt \
    && uv pip install --system --no-deps .

WORKDIR /app/servers/nextjs
COPY servers/nextjs/package.json servers/nextjs/package-lock.json ./
RUN npm install
COPY servers/nextjs/ /app/servers/nextjs/
RUN npm run build

WORKDIR /app
COPY start.js LICENSE NOTICE ./
COPY nginx.conf /etc/nginx/nginx.conf

EXPOSE 80
CMD ["node", "/app/start.js"]
