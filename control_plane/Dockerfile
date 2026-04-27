FROM python:3.11-slim
WORKDIR /app
COPY control_plane/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN pip install --no-cache-dir aiofiles==23.2.1
COPY control_plane/main.py .
COPY agent/ ./agent/
COPY control_plane/static/ ./static/
EXPOSE 8080
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
