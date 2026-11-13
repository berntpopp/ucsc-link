# syntax=docker/dockerfile:1
# Multi-stage Dockerfile for ucsc-link.
#
# ucsc-link exposes read-only tools over the live UCSC Genome Browser JSON API
# (https://api.genome.ucsc.edu). The image ships NO data and keeps no on-disk state:
# responses are cached in-process only.

FROM python:3.14-slim@sha256:9aaa474097d094040649f2162a627c0049b408f52138d980916788d80f04802f AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

RUN apt-get update \
    && apt-get upgrade -y --no-install-recommends \
    && apt-get install -y --no-install-recommends \
    build-essential \
    ca-certificates \
    curl \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv
ENV VIRTUAL_ENV="/opt/venv" \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /home/app/web
COPY uv.lock pyproject.toml README.md ./

# F-19: install uv from a digest-pinned image (reproducible) instead of the
# previous unbounded floating pip/uv bootstrap.
COPY --from=ghcr.io/astral-sh/uv:0.8.7@sha256:1e26f9a868360eeb32500a35e05787ffff3402f01a8dc8168ef6aee44aef0aab /uv /usr/local/bin/uv

RUN uv sync --frozen --no-dev --active --no-install-project


FROM python:3.14-slim@sha256:9aaa474097d094040649f2162a627c0049b408f52138d980916788d80f04802f AS prepared

# UCSC_LINK_ALLOW_PUBLIC_BIND=true is the explicit, audited opt-in that the
# F-04 bind guard requires: inside the container the server intentionally binds
# 0.0.0.0, but it is only ever reached via the loopback-published port (base
# compose) or the reverse proxy (prod/npm overlays), never the host's public IP.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONPATH="/home/app/web" \
    UCSC_LINK_TRANSPORT=unified \
    UCSC_LINK_HOST=0.0.0.0 \
    UCSC_LINK_ALLOW_PUBLIC_BIND=true \
    UCSC_LINK_PORT=8000 \
    UCSC_LINK_MCP_PATH=/mcp \
    UCSC_LINK_LOG_FORMAT=json

RUN apt-get update \
    && apt-get upgrade -y --no-install-recommends \
    && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

# /opt/blat is the mount point of the BLAT volume (docker-compose.npm.yml): an empty,
# app-owned directory so a fresh named volume inherits uid 10001. The Kent binaries and
# .2bit genomes are fetched onto it at deploy time and never shipped in this image.
RUN groupadd --gid 10001 app && \
    useradd --uid 10001 --gid app --home /home/app --create-home --shell /usr/sbin/nologin app && \
    chown -R app:app /home/app && \
    install -d -o app -g app -m 0755 /opt/blat

COPY --from=builder /opt/venv /opt/venv

WORKDIR /home/app/web

COPY --chown=app:app ./ucsc_link ./ucsc_link
COPY --chown=app:app ./mcp_server.py ./server.py ./pyproject.toml ./README.md ./

RUN /opt/venv/bin/pip install . --no-deps

RUN rm -rf /root/.cache/pip \
           /usr/local/lib/python*/ensurepip \
           /usr/local/lib/python*/site-packages/pip \
           /usr/local/lib/python*/site-packages/pip-*.dist-info \
           /usr/local/bin/pip /usr/local/bin/pip3 /usr/local/bin/pip3.* \
           /opt/venv/lib/python*/site-packages/pip \
           /opt/venv/lib/python*/site-packages/pip-*.dist-info \
           /opt/venv/bin/pip /opt/venv/bin/pip3 /opt/venv/bin/pip3.* \
    && find / -xdev -type d -name __pycache__ -prune -exec rm -rf '{}' + \
    && find / -xdev -type f \( -name '*.pyc' -o -name '*.pyo' \) -delete

RUN find / -xdev -perm /6000 -exec chmod a-s '{}' + \
    && test -z "$(find / -xdev -perm /6000 -print -quit)"

RUN actual="$(find / -xdev -type f -links +1 -printf '%i %p\n' | sort -k2,2)" \
    && expected="$(printf '%s %s\n%s %s\n' \
         "$(stat -c '%i' /usr/bin/perl)" /usr/bin/perl \
         "$(stat -c '%i' /usr/bin/perl5.40.1)" /usr/bin/perl5.40.1 | sort -k2,2)" \
    && { test "$actual" = "$expected" || { printf 'unexpected hardlinks:\n%s\n' "$actual"; exit 1; }; } \
    && rm -f /usr/bin/perl5.40.1

FROM scratch AS production
COPY --from=prepared / /

ARG APP_VERSION=0.0.0
ARG VCS_REF=unknown
ARG BUILD_DATE=1970-01-01T00:00:00Z
LABEL org.opencontainers.image.title="ucsc-link" \
      org.opencontainers.image.description="Research-use MCP server for curated UCSC Genome Browser region tracks and reference sequence." \
      org.opencontainers.image.source="https://github.com/berntpopp/ucsc-link" \
      org.opencontainers.image.url="https://github.com/berntpopp/ucsc-link" \
      org.opencontainers.image.documentation="https://github.com/berntpopp/ucsc-link#readme" \
      org.opencontainers.image.version="${APP_VERSION}" \
      org.opencontainers.image.revision="${VCS_REF}" \
      org.opencontainers.image.created="${BUILD_DATE}" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.vendor="GeneFoundry"
ENV APP_VERSION="${APP_VERSION}" \
    VCS_REF="${VCS_REF}" \
    UCSC_LINK_GIT_SHA="${VCS_REF}" \
    UCSC_LINK_BUILT_AT="${BUILD_DATE}"

# UCSC_LINK_ALLOW_PUBLIC_BIND=true is the explicit, audited opt-in that the
# F-04 bind guard requires: inside the container the server intentionally binds
# 0.0.0.0, but it is only ever reached via the loopback-published port (base
# compose) or the reverse proxy (prod/npm overlays), never the host's public IP.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:/usr/local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin" \
    PYTHONPATH="/home/app/web" \
    UCSC_LINK_TRANSPORT=unified \
    UCSC_LINK_HOST=0.0.0.0 \
    UCSC_LINK_ALLOW_PUBLIC_BIND=true \
    UCSC_LINK_PORT=8000 \
    UCSC_LINK_MCP_PATH=/mcp \
    UCSC_LINK_LOG_FORMAT=json

WORKDIR /home/app/web

USER app

EXPOSE 8000

# Short start-period: no data to load — the server starts immediately.
# The healthcheck confirms /health responds after uvicorn is ready.
HEALTHCHECK --interval=30s --timeout=10s --retries=5 --start-period=30s \
    CMD curl -f -H 'Host: localhost' http://127.0.0.1:8000/health || exit 1

CMD ["ucsc-link", "--transport", "unified", "--host", "0.0.0.0", "--port", "8000"]
