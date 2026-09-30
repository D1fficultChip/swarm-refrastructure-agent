FROM node:22-alpine AS frontend-build
WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.10-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    FRONTEND_DIST=/app/frontend/dist
WORKDIR /app

COPY pyproject.toml ./
COPY backend/ ./backend/
RUN python -m pip install --no-cache-dir .

COPY config/ ./config/
COPY scenarios/ ./scenarios/
COPY --from=frontend-build /build/frontend/dist ./frontend/dist/

RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p artifacts/phase6/demo/runs artifacts/phase62/dynamic \
    && chown -R appuser:appuser /app/artifacts
USER appuser

EXPOSE 10000
CMD ["sh","-c","python -m uvicorn backend.app.main:app --host 0.0.0.0 --port ${PORT:-10000}"]
