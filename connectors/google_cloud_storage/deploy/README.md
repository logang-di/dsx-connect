# Google Cloud Storage Connector — Deployment

Choose one deployment method:

- Docker Compose: see `deploy/docker/DEPLOYMENT_GUIDE_DOCKER.md`
- Helm (Kubernetes): see `deploy/helm/DEPLOYMENT_GUIDE_K8S.md`

Local builds: `deploy/docker/Dockerfile` and `deploy/docker/requirements.txt` are provided for building images locally (e.g., with `docker build`).

Notes:
- For local IDE/debug, see the root dsx-connect README for `.dev.env` (sets `GOOGLE_APPLICATION_CREDENTIALS` and `DSXCONNECTOR_ASSET`).
- The connector uses Google Application Default Credentials (ADC). Provide credentials via a Service Account JSON key (Compose/Helm Secret) or use Workload Identity on GKE.
