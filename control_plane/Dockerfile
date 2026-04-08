# ── Stage 1: Build React app ──────────────────────────────────────────────────
FROM node:20-slim AS frontend-build

WORKDIR /app/frontend

# Copy frontend source
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci --silent

COPY frontend/ ./
RUN npm run build

# ── Stage 2: Python backend + serve static frontend ───────────────────────────
FROM python:3.11-slim

WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy FastAPI backend
COPY main.py .

# Copy built React app into static/ folder
COPY --from=frontend-build /app/frontend/dist ./static

# Serve static files from FastAPI
RUN pip install --no-cache-dir aiofiles==23.2.1

EXPOSE 8080

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
