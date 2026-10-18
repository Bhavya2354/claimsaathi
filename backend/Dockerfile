# Production image for the API. Built by the host (for example Hugging Face Spaces).
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    ENV_FILE=""

RUN apt-get update \
    && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.12 /uv /usr/local/bin/uv

RUN useradd --create-home --uid 1000 app
WORKDIR /srv
COPY --chown=app:app pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project
COPY --chown=app:app . .
RUN uv sync --locked --no-dev && chown -R app:app /srv

USER app
ENV PATH="/srv/.venv/bin:$PATH"
# Required at runtime: PORT, DATABASE_URL, model routes and keys, retrieval and privacy settings.
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port \"$PORT\" --proxy-headers --forwarded-allow-ips='*'"]
