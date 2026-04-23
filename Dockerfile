# syntax=docker/dockerfile:1.7

# First stage builds the virtualenv with compilers available.
FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

# System libraries needed to build asyncpg, cryptography and gitpython.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        libpq-dev \
        git \
    && rm -rf /var/lib/apt/lists/*

COPY requirements/ ./requirements/

ARG REQUIREMENTS=prod
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --upgrade pip \
    && /opt/venv/bin/pip install -r requirements/${REQUIREMENTS}.txt


# Runtime stage only carries what the application needs to run.
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONPATH=/app

# Runtime libraries + Node.js (required by Claude Agent SDK).
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libpq5 \
        git \
        curl \
        nodejs \
        npm \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system app \
    && useradd --system --gid app --create-home --shell /bin/bash app

# Install Claude Code CLI globally (Agent SDK requires it)
RUN npm install -g @anthropic-ai/claude-code@latest 2>/dev/null || true

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
COPY --chown=app:app . /app

USER app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl --fail http://localhost:8000/api/v1/health || exit 1

CMD ["uvicorn", "src.api.app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
