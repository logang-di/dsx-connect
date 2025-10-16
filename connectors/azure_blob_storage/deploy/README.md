# Azure Blob Storage Connector — Deployment

Choose one deployment method:

- Docker Compose: see `deploy/docker/README.md`
- Helm (Kubernetes): see `deploy/helm/README.md`

Note on assets and prefixes: set `DSXCONNECTOR_ASSET` to `container` or `container/prefix`. When a prefix is provided, listings start at that sub-root and filters are evaluated relative to it. See the Sharding & Deployment Strategies appendix in each deployment README for examples.

Local builds: `deploy/docker/Dockerfile` and (if present) `deploy/docker/requirements.txt` are provided for building images locally.

Notes:
- For secrets and connection strings, prefer environment variables or Kubernetes Secrets. See the Helm README for guidance.
