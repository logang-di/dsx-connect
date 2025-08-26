FROM python:3.12-slim

# Create a non-root user
RUN useradd -m appuser

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

RUN mkdir quarantine
RUN mkdir scan_folder

COPY connectors/framework connectors/framework
COPY connectors/filesystem connectors/filesystem
COPY shared/ shared/
COPY connectors/framework/deploy/certs certs/
COPY start.py .

ENV PYTHONPATH=/app
RUN mkdir -p data && chown appuser:appuser data

USER appuser

# Command is set in docker-compose-filesystem
