# Docker Compose Quickstart

This page summarizes the minimal steps to spin up DSX‑Connect connectors with Docker Compose for proofs of value (POV) and testing.

## 1) Create a shared network
```bash
docker network create dsx-connect-network || true
```

## 2) Run dsx‑connect (Core)
- If you already have dsx‑connect running (API + workers) on the same Docker network, you can skip this step.
- Otherwise, deploy dsx‑connect on `dsx-connect-network` so connectors can resolve `dsx-connect-api`.
- For Docker Compose deployments, keep authentication disabled (do not set dsx‑connect auth/enrollment variables in Compose).

## 3) Deploy a connector (examples)

AWS S3:
```bash
docker compose -f connectors/aws_s3/deploy/docker/docker-compose-aws-s3-connector.yaml up -d
```

Azure Blob:
```bash
docker compose -f connectors/azure_blob_storage/deploy/docker/docker-compose-azure-blob-storage-connector.yaml up -d
```

Filesystem:
```bash
docker compose -f connectors/filesystem/deploy/docker/docker-compose-filesystem-connector.yaml up -d
```

Google Cloud Storage:
```bash
docker compose -f connectors/google_cloud_storage/deploy/docker/docker-compose-google-cloud-storage-connector.yaml up -d
```

SharePoint:
```bash
docker compose -f connectors/sharepoint/deploy/docker/docker-compose-sharepoint-connector.yaml up -d
```

## 4) Verify registration
- Connectors expose `/readyz` and `/healthz` endpoints.
- In dsx‑connect, verify the connector appears in the connector list and is READY.

## 5) Asset and Filters
- Set `DSXCONNECTOR_ASSET` to the stable, exact root of your scan (no wildcards).
- Use `DSXCONNECTOR_FILTER` for rsync‑like scoping under that root.
- See Reference → [Assets & Filters](../../reference/assets-and-filters.md) and [Filters (Details)](../../reference/filters.md).

## 6) Security notes
- Ideally, don't hardcode long‑lived credentials into compose files; pass via secrets/env or your secret manager.
- For TLS to dsx‑connect, mount the CA bundle and set `DSXCONNECTOR_VERIFY_TLS=true` and `DSXCONNECTOR_CA_BUNDLE=...`.
