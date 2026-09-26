FROM node:22-alpine AS frontend
WORKDIR /app/frontend
COPY frontend/package*.json ./
RUN npm install
COPY frontend/ ./
RUN npm run build

FROM ghcr.io/astral-sh/uv:python3.11-bookworm-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1 PORT=8080 SOURCELENS_DATA_DIR=/tmp/sourcelens
RUN apt-get update && apt-get install -y --no-install-recommends antiword && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml uv.lock* ./
RUN uv sync --no-dev --no-install-project
COPY src ./src
COPY --from=frontend /app/frontend/dist ./frontend/dist
RUN uv sync --no-dev
EXPOSE 8080
CMD ["sh", "-c", "uv run uvicorn sourcelens.main:app --host 0.0.0.0 --port ${PORT}"]
