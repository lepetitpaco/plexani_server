# ── Stage 1 : Build frontend React/Vite ─────────────────────────────────────
FROM node:20-alpine AS frontend-builder

WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install --frozen-lockfile 2>/dev/null || npm install

COPY frontend/ .
RUN npm run build


# ── Stage 2 : Backend Python + frontend buildé ───────────────────────────────
FROM python:3.12-slim AS runtime

# Dépendances système pour plexapi (SSL, etc.)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libssl-dev ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dépendances Python
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Code backend
COPY backend/ ./backend/

# Frontend buildé
COPY --from=frontend-builder /app/frontend/dist ./frontend/dist

# Volume pour données persistantes (config + historique)
VOLUME ["/data"]

ENV DATA_DIR=/data
ENV PYTHONPATH=/app/backend
ENV PYTHONUNBUFFERED=1

EXPOSE 8765

CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8765"]
