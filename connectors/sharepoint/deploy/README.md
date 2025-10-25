# SharePoint Connector — Deployment

Choose one deployment method:

- Docker Compose: see `deploy/docker/DEPLOYMENT_GUIDE_DOCKER.md`
- Helm (Kubernetes): see `deploy/helm/DEPLOYMENT_GUIDE_K8S.md`

Local builds: `deploy/docker/Dockerfile` and (if present) `deploy/docker/requirements.txt` are provided for building images locally.

Notes:
- For Microsoft Graph credentials and secrets, prefer environment variables or Kubernetes Secrets. See the Helm README for guidance.
