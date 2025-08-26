FROM python:3.12-slim

# Create a non-root user
RUN useradd -m appuser

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY connectors/framework connectors/framework
COPY connectors/{{ cookiecutter.project_slug }} connectors/{{ cookiecutter.project_slug }}
COPY shared/ shared/
COPY connectors/framework/deploy/certs certs/

ENV PYTHONPATH=/app
RUN mkdir -p data && chown appuser:appuser data

USER appuser

# Command is set in docker-compose-{{ cookiecutter.__release_name }}.yaml
