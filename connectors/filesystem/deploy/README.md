# Filesystem Connector — Deployment

Choose one deployment method:

- Docker Compose: see `deploy/docker/README.md`
- Helm (Kubernetes): see `deploy/helm/README.md`

Local builds: `deploy/docker/Dockerfile` and (if present) `deploy/docker/requirements.txt` are provided for building images locally.

Notes:
- For local IDE/debug, see the root dsx-connect README for `.dev.env` tips and running with Uvicorn locally.
