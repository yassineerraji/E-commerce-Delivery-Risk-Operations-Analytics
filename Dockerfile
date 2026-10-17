# Dashboard image (read-only access to PostgreSQL). Analysis commands run on the host with uv.
FROM python:3.12.13-slim-bookworm

# libgomp1: OpenMP runtime required by the LightGBM wheel (imported by the shared code base).
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.11.29 /uv /uvx /bin/

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    DR_PROJECT_ROOT=/app \
    PYTHONUNBUFFERED=1

COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
COPY config ./config
COPY sql ./sql
RUN uv sync --frozen --no-dev

RUN useradd --create-home --uid 10001 app
USER app
EXPOSE 8050
# PORT and WEB_CONCURRENCY are provided by hosting platforms such as Render (defaults for local use).
CMD ["sh", "-c", "exec /app/.venv/bin/gunicorn --bind 0.0.0.0:${PORT:-8050} --workers ${WEB_CONCURRENCY:-2} --timeout 60 --access-logfile - delivery_risk.dashboard.wsgi:server"]
