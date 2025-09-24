FROM python:3.12-slim

# Create a non-root user
RUN useradd -m appuser

WORKDIR /app

# Requirements are kept under deploy/docker for this connector
COPY docker/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY connectors/framework connectors/framework
COPY connectors/google_cloud_storage connectors/google_cloud_storage
COPY shared/ shared/
COPY certs/ certs/
# Ensure certs are readable by appuser (private key restricted to owner)
RUN chown -R appuser:appuser certs \
    && chmod 0644 certs/*.crt || true \
    && chmod 0600 certs/*.key || true

ENV PYTHONPATH=/app
RUN mkdir -p data && chown appuser:appuser data

USER appuser

# Command is set via compose or Helm

