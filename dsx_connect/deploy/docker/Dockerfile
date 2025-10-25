ARG PY_BASE_IMAGE=python:3.12-slim-bookworm
FROM ${PY_BASE_IMAGE}

WORKDIR /app

# Ensure base OS crypto is up to date (addresses OpenSSL CVEs flagged by scanners)
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       openssl \
       ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Optional: allow overriding PyPI index via build args
ARG PIP_INDEX_URL
ARG PIP_EXTRA_INDEX_URL
ENV PIP_INDEX_URL=${PIP_INDEX_URL}
ENV PIP_EXTRA_INDEX_URL=${PIP_EXTRA_INDEX_URL}
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY dsx_connect/ dsx_connect/
COPY shared/ shared/
COPY certs/ certs/

# Create non-root user and data directory; set ownership
RUN useradd -m -r -s /bin/bash dsxconnectuser \
    && mkdir -p /app/data \
    && chown -R dsxconnectuser:dsxconnectuser /app

ENV PYTHONPATH=/app

USER dsxconnectuser

# Command is set in docker-compose-dsx-connect-all-services.yaml
