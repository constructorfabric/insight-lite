# --- Stage 1: build the React frontend (Vite) -------------------------------
# Not present in the final image — no Node at runtime. Output: assets/app/
# (hashed JS/CSS + .vite/manifest.json), consumed by spa.py/server.py.
FROM node:22-slim AS frontend
WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
# vite.config.ts build.outDir = "../assets/app" (relative to frontend/), so
# this lands at /build/assets/app.
RUN npm run build

# --- Stage 2: the Python app -------------------------------------------------
FROM python:3.12-slim

WORKDIR /work

RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Code + templates only — .dockerignore excludes all runtime state (report.db*,
# people.yaml, .cache/, .repos/, .runtime/, history/, exports/, data.json,
# report.html, .env, frontend/node_modules, assets/app) so nothing stateful is
# baked into the image. All runtime state instead lives under DATA_DIR, a
# volume mounted at container start (see docker-compose.yml).
COPY . .

# The built frontend always comes from the frontend stage, never from a stale
# host-side assets/app/ (which .dockerignore excludes from the build context
# anyway) — copied last so it's the final word regardless.
COPY --from=frontend /build/assets/app /work/assets/app

EXPOSE 8080

# Bind 0.0.0.0 inside the container so published ports work; the compose file
# maps it to 127.0.0.1 on the host.
ENV PORTAL_HOST=0.0.0.0
# All runtime state (report.db, people.yaml, caches, clones, history/,
# exports/, generated report.html/data.json) lives here — mount a persistent
# volume at this path in compose so an image swap never loses data.
ENV DATA_DIR=/work/data

CMD ["python", "reportctl.py", "serve", "--port", "8080"]
