FROM python:3.12-alpine3.20

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY dsx_connect/ dsx_connect/

# Create data directory and set ownership
RUN mkdir -p /app/data && \
    adduser -D -h /home/dsxconnectuser dsxconnectuser && \
    chown -R dsxconnectuser:dsxconnectuser /app

ENV PYTHONPATH=/app

USER dsxconnectuser

# Command is set in docker-compose.yaml