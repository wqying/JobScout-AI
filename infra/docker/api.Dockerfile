FROM ghcr.io/astral-sh/uv:0.12.2 AS uv

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app
COPY --from=uv /uv /uvx /bin/
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev
COPY alembic.ini ./
COPY alembic ./alembic
COPY app ./app
RUN useradd --create-home --uid 10001 jobscout \
    && chown -R jobscout:jobscout /app

USER jobscout

EXPOSE 8000
