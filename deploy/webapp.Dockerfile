# The webapp image deliberately never installs or copies the native Mac engine.
# Build static browser assets natively; keep Vite+ checks out of QEMU.
FROM --platform=$BUILDPLATFORM node:24.22.1-bookworm-slim AS frontend
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json frontend/.node-version ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM ghcr.io/astral-sh/uv:0.13.1 AS uv
FROM python:3.12.15-slim AS runtime
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never \
    PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    NOX_VOICE_STATIC_DIR=/app/frontend/dist NOX_VOICE_DATA_DIR=/data \
    PATH=/app/gateway/.venv/bin:$PATH
WORKDIR /app/gateway
COPY gateway/pyproject.toml gateway/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY gateway/nox_voice_gateway/ ./nox_voice_gateway/
RUN uv sync --frozen --no-dev \
    && groupadd --gid 10001 noxvoice \
    && useradd --uid 10001 --gid 10001 --no-create-home noxvoice \
    && mkdir /data && chown 10001:10001 /data
COPY --from=frontend /build/dist /app/frontend/dist
# The bootstrap copies mounted secrets into /tmp, then switches to UID/GID 10001.
# The HTTP server never runs as root. Host secret ownership is left untouched.
USER 0:0
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=3)"]
ENTRYPOINT ["python", "-m", "nox_voice_gateway.bootstrap"]
