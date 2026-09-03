FROM python:3.11-slim AS builder
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .

FROM python:3.11-slim
WORKDIR /app
RUN useradd --create-home appuser
COPY --from=builder /usr/local /usr/local
COPY alembic.ini ./
COPY alembic ./alembic
COPY config ./config
COPY tests/fixtures/offline-cache ./tests/fixtures/offline-cache
COPY src ./src
USER appuser
EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && uvicorn strategy_redteam.api.app:app --host 0.0.0.0 --port 8000"]
