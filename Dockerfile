FROM node:22-bookworm-slim AS web-builder

WORKDIR /build/web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build


FROM python:3.13-slim-bookworm AS runtime

ENV PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

RUN apt-get update \
    && apt-get install --yes --no-install-recommends ca-certificates ffmpeg \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 catvideo \
    && useradd --uid 10001 --gid 10001 --create-home catvideo

COPY --from=ghcr.io/astral-sh/uv:0.9.5 /uv /uvx /bin/

WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY src/ ./src/
RUN uv sync --frozen --no-dev

COPY alembic.ini ./
COPY alembic/ ./alembic/
COPY scripts/archive_v3_and_clear.py ./scripts/archive_v3_and_clear.py
COPY --from=web-builder /build/web/dist ./web-dist/

RUN mkdir -p /data/work /data/assets \
    && chown -R catvideo:catvideo /app /data

USER catvideo
EXPOSE 8765

CMD ["sh", "-c", "alembic upgrade head && cvg api --host 0.0.0.0 --port 8765 --static-dir /app/web-dist"]
