# Project Athena — multi-stage Dockerfile.
#
# Targets (select with `--target <name>` in docker build or
# `target: <name>` in docker-compose.yml):
#
#   api-base  : Python base with all backend dependencies installed
#               and the sentence-transformers model pre-downloaded.
#   api       : the runtime API image (non-root, uvicorn)
#   web-base  : Node base with frontend dependencies
#   web       : production SPA build → static dist/
#   web-dev   : Vite dev server (HMR) — opt-in via `--profile dev`
#   nginx     : production reverse proxy + static server
#
# Build any one with:
#   docker build --target api    -t athena/api    .
#   docker build --target web    -t athena/web    .
#   docker build --target nginx  -t athena/nginx  .
# Or bring the whole stack up with:
#   docker compose up -d --build

# syntax=docker/dockerfile:1.7
ARG PY_VERSION=3.11
ARG NODE_VERSION=20


# ============================================================================
# api-base — Python runtime + Python deps + (best-effort) embedding model
# ============================================================================
FROM python:${PY_VERSION}-slim AS api-base

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONHASHSEED=random

WORKDIR /app

# System deps:
#   - build-essential, gcc, libpq-dev: build psycopg / pgvector / bcrypt wheels
#   - curl: HEALTHCHECK + ollama probe
#   - ca-certificates: HTTPS
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        build-essential \
        gcc \
        libpq-dev \
        curl \
        ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# Non-root user. Create it early so the layer cache survives.
RUN groupadd --gid 1000 athena \
 && useradd  --uid 1000 --gid athena --shell /bin/bash --create-home athena

# Install Python deps (this layer is cached unless requirements.txt changes).
COPY backend/requirements.txt ./backend/requirements.txt
RUN pip install -r backend/requirements.txt

# Bake model caches to a NON-home path. (Set AFTER `pip install` so the
# pip layer stays cached — these ENVs only need to be visible to the
# pre-download RUNs below and to the runtime `api` stage.) The runtime
# process runs as `athena` (HOME=/home/athena); defaulting HF_HOME /
# tiktoken cache to ~/.cache would put them on the read-only rootfs
# (read_only:true) and crash on first use with [Errno 30]. /opt/hf-cache
# and /opt/tiktoken-cache are baked into the image (read-only at runtime)
# and only ever READ at runtime (HF_HUB_OFFLINE / TRANSFORMERS_OFFLINE
# are set in the `api` stage), so they never need to be writable. Neither
# path is under a tmpfs mount, so the baked content is never shadowed.
ENV HF_HOME=/opt/hf-cache \
    TIKTOKEN_CACHE_DIR=/opt/tiktoken-cache

# Pre-download the sentence-transformers model so the first request after a
# cold start is fast. Failures are non-fatal so the image can still build
# on a machine without network access (e.g. a hermetic CI). HF_HOME (set
# above) routes this download into /opt/hf-cache, which the runtime `athena`
# user reads from the read-only rootfs.
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('sentence-transformers/all-MiniLM-L6-v2')" \
 || echo "WARN: embedding model pre-download failed; will be fetched on first use"

# Pre-bake tiktoken's cl100k_base BPE encoding. app/services/text.py calls
# tiktoken.get_encoding("cl100k_base") at module import (i.e. at app STARTUP,
# not only at ingestion), so without this the read-only container would
# need network on every cold start to fetch it. TIKTOKEN_CACHE_DIR (set
# above) routes the download into /opt/tiktoken-cache. Best-effort, same
# non-fatal fallback as the embedding model.
RUN python -c "import tiktoken; tiktoken.get_encoding('cl100k_base')" \
 || echo "WARN: tiktoken encoding pre-download failed; will be fetched on first use"

# The pre-downloads above ran as root (api-base has no USER directive). The
# runtime process is `athena` (uid 1000), reading these caches from the
# read-only rootfs — chown so athena can read them. Guarded (2>/dev/null ||
# true) so a best-effort download that left a dir absent doesn't fail the
# build.
RUN chown -R athena:athena /opt/hf-cache /opt/tiktoken-cache 2>/dev/null || true


# ============================================================================
# api — runtime image for the FastAPI app
# ============================================================================
FROM api-base AS api

# Force HF/transformers into offline mode at runtime. The embedding model
# is baked into /opt/hf-cache (read-only rootfs) by api-base; without
# these flags huggingface_hub would still reach the hub to verify the
# snapshot and try to write metadata into the (read-only) cache — i.e.
# [Errno 30] even though the model is already present. Offline makes the
# load a pure read from the baked cache: no network, no writes, fast and
# read-only-safe. Inherited HF_HOME + TIKTOKEN_CACHE_DIR point at the
# baked caches. (Hermetic builds where the pre-download failed will get
# a clear "model not found in offline cache" error instead of errno 30 —
# no model means no embeddings, which is inherent.)
ENV HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1

# gosu lets the entrypoint drop privileges after fixing volume
# ownership. We install it now (root) so the runtime stage is
# self-contained.
RUN apt-get update \
 && apt-get install -y --no-install-recommends gosu \
 && rm -rf /var/lib/apt/lists/* \
 && gosu --version

WORKDIR /app

# Copy the backend. We copy the whole tree (it's small) and rely on
# .dockerignore to keep the build context clean.
COPY --chown=athena:athena backend/ ./backend/

# Entrypoint runs as root to fix ownership on the mounted storage
# volume, then drops to the `athena` user.
COPY --chmod=0755 infra/entrypoint-api.sh /usr/local/bin/entrypoint-api.sh

# Make 'app.*' importable (the FastAPI entrypoint is `main:app` at /app,
# so we copy the package files into /app rather than /app/backend).
WORKDIR /app/backend

EXPOSE 8000

# M-25 — declare USER last so the `athena` user owns the runtime
# context. The entrypoint script re-execs to `athena` via gosu
# after fixing storage ownership, but declaring USER here gives
# the image a sane default if a caller overrides the entrypoint
# (e.g. ``docker run --entrypoint /bin/sh``). The image now has
# *no* code path that runs uvicorn as root.
USER athena

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:8000/health || exit 1

ENTRYPOINT ["/usr/local/bin/entrypoint-api.sh"]
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]


# ============================================================================
# web-base — Node base with frontend deps installed
# ============================================================================
FROM node:${NODE_VERSION}-alpine AS web-base

WORKDIR /app

# libc6-compat lets alpine run sharp / esbuild binaries (if added later).
RUN apk add --no-cache libc6-compat

# Install deps first for layer cache.
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install --no-audit --no-fund


# ============================================================================
# web — production SPA build
# ============================================================================
FROM web-base AS web

WORKDIR /app

# VITE_API_TARGET tells the dev proxy (and any code that reads it at build
# time) where the API lives. In production, the SPA uses relative paths
# (/api/...) because nginx reverse-proxies.
ARG VITE_API_TARGET=/api
ENV VITE_API_TARGET=$VITE_API_TARGET

COPY frontend/ ./
RUN npm run build

# `web` is a build-only stage. The output (dist/) is consumed by the
# `nginx` stage via `COPY --from=web`.


# ============================================================================
# web-dev — Vite dev server (HMR). Use with: docker compose --profile dev up
# ============================================================================
FROM web-base AS web-dev

WORKDIR /app
ENV HOST=0.0.0.0

COPY frontend/ ./

EXPOSE 5173

CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0"]


# ============================================================================
# nginx — production reverse proxy + static server
# ============================================================================
FROM nginx:1.27-alpine AS nginx

# Replace the default site config.
COPY infra/nginx-prod.conf /etc/nginx/conf.d/default.conf

# Static bundle from the `web` stage. BuildKit resolves the stage name.
COPY --from=web /app/dist /usr/share/nginx/html

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=5s --retries=3 --start-period=10s \
    CMD curl -fsS http://127.0.0.1/ >/dev/null || exit 1
