# SolarSync - single image: Next.js frontend (public port) + FastAPI backend (internal)

# ---------- stage 1: build the frontend ----------
FROM node:22-bookworm-slim AS frontend
WORKDIR /web
ENV NEXT_TELEMETRY_DISABLED=1 \
    NEXT_PUBLIC_API_URL=/api/v1
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ---------- stage 2: runtime ----------
FROM node:22-bookworm-slim AS runtime
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=8080 \
    BACKEND_ORIGIN=http://127.0.0.1:8000

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        python3 python3-venv ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY backend/requirements.txt ./
RUN python3 -m venv /opt/venv \
    && /opt/venv/bin/pip install --upgrade pip \
    && /opt/venv/bin/pip install -r requirements.txt
ENV PATH="/opt/venv/bin:$PATH"

COPY backend/app ./app
COPY deploy/start.sh /usr/local/bin/start.sh
RUN chmod +x /usr/local/bin/start.sh

WORKDIR /app/web
COPY --from=frontend /web/.next/standalone ./
COPY --from=frontend /web/.next/static ./.next/static

WORKDIR /app
EXPOSE 8080
CMD ["/usr/local/bin/start.sh"]
