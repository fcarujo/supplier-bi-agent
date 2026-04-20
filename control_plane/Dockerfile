FROM node:20-slim AS frontend-build
WORKDIR /app/frontend
COPY control_plane/frontend/package.json control_plane/frontend/package-lock.json* ./
RUN npm ci --silent
COPY control_plane/frontend/ ./
RUN npm run build

FROM python:3.11-slim
WORKDIR /app
COPY control_plane/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN pip install --no-cache-dir aiofiles==23.2.1
COPY control_plane/main.py .
COPY agent/ ./agent/
COPY --from=frontend-build /app/frontend/dist ./static
EXPOSE 8080
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]