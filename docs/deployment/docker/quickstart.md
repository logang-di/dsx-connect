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

## 4) Reuse `.env` files instead of editing compose YAML
Keeping credentials in a separate env file lets you refresh docker-compose YAML without re-entering secrets.

1. **Create a connector-specific env file**
   ```bash
   cat <<'EOF' > .env.aws-s3
   LOG_LEVEL=debug
   DSXCONNECTOR_ASSET=lg-test-01
   DSXCONNECTOR_FILTER=
   AWS_ACCESS_KEY_ID=AKIAxxxxxxxxxxxx
   AWS_SECRET_ACCESS_KEY=xxxxxxxxxxxxxxxx
   DSXCONNECT_ENROLLMENT_TOKEN=abc123
   EOF
   ```
   - Use `KEY=value` lines; only quote values that contain spaces.
   - Keep separate files per environment (e.g., `.env.aws-s3.prod`).

2. **Point Docker Compose at the file**
   ```yaml
   services:
     aws_s3_connector:
       image: dsxconnect/aws-s3-connector:__VERSION__
       env_file:
         - .env.aws-s3
   ```
   Or use `docker compose --env-file .env.aws-s3 ...`. Compose merges the file with the inline `environment:` block.

3. **Reuse the same file when you switch to Kubernetes**
   ```bash
   kubectl create secret generic aws-s3-connector-env \
     --from-env-file=.env.aws-s3 \
     --namespace your-namespace
   ```
   Then set `envSecretRefs[0]=aws-s3-connector-env` in the Helm values. This keeps Secrets consistent across deployment methods.

## 5) Verify registration
- Connectors expose `/readyz` and `/healthz` endpoints.
- In dsx‑connect, verify the connector appears in the connector list and is READY.

## 6) Asset and Filters
- Set `DSXCONNECTOR_ASSET` to the stable, exact root of your scan (no wildcards).
- Use `DSXCONNECTOR_FILTER` for rsync‑like scoping under that root.
- See Reference → [Assets & Filters](../../reference/assets-and-filters.md) and [Filters (Details)](../../reference/filters.md).

## 7) Security notes
- Ideally, don't hardcode long‑lived credentials into compose files; pass via secrets/env or your secret manager.
- For TLS to dsx‑connect, mount the CA bundle and set `DSXCONNECTOR_VERIFY_TLS=true` and `DSXCONNECTOR_CA_BUNDLE=...`.
