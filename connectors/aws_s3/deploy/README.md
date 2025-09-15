# AWS S3 Connector — Deployment

Choose one deployment method:

- Docker Compose: see `deploy/docker/README.md`
- Helm (Kubernetes): see `deploy/helm/README.md`

Local builds: `deploy/docker/Dockerfile` and (if present) `deploy/docker/requirements.txt` are provided for building images locally.

Notes:
- For secrets and credentials, prefer environment variables or Kubernetes Secrets. See the Helm README for guidance.
