FROM python:3.12-slim

# Create a non-root user
RUN useradd -m appuser

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY connectors/framework connectors/framework
COPY connectors/azure_blob_storage connectors/azure_blob_storage
COPY dsx_connect/ dsx_connect/
COPY start.py .

ENV PYTHONPATH=/app

USER appuser

# Command is set in docker-compose.yaml
