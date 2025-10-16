ARG PY_BASE_IMAGE=python:3.12-slim
FROM ${PY_BASE_IMAGE}

# Create a non-root user
RUN useradd -m appuser

WORKDIR /app

# Optional: allow overriding PyPI index via build args
ARG PIP_INDEX_URL
ARG PIP_EXTRA_INDEX_URL
ENV PIP_INDEX_URL=${PIP_INDEX_URL}
ENV PIP_EXTRA_INDEX_URL=${PIP_EXTRA_INDEX_URL}

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY connectors/framework connectors/framework
COPY connectors/aws_s3 connectors/aws_s3
COPY shared/ shared/
COPY certs/ certs/
# Ensure certs are readable by appuser (private key restricted to owner)
RUN chown -R appuser:appuser certs \
    && chmod 0644 certs/*.crt || true \
    && chmod 0600 certs/*.key || true

ENV PYTHONPATH=/app
RUN mkdir -p data && chown appuser:appuser data

USER appuser

# Command is set in docker-compose-dsx-connect-all-services.yaml
