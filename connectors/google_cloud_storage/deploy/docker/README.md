# Deploy with Docker Compose

This guide shows how to run the Google Cloud Storage connector with Docker Compose.

## Prerequisites
- A Google Cloud service account JSON file with the required roles
  - Read-only: `roles/storage.objectViewer`
  - Tag/Move/Delete: add `roles/storage.objectAdmin` (or a tighter custom role)
- A docker network shared with dsx-connect (example below uses `dsx-connect-network`).

## Example docker-compose service

Use the provided `deploy/docker/docker-compose-google-cloud-storage-connector.yaml` as a starting point. Adjust:
- `DSXCONNECTOR_ASSET` to your bucket or bucket/prefix.
- `DSXCONNECTOR_FILTER` for scoping; see filter rules below.
- Mount your service‑account JSON and set `GOOGLE_APPLICATION_CREDENTIALS` (example below).

Credentials (Application Default Credentials):
```yaml
services:
  google_cloud_storage_connector:
    volumes:
      - type: bind
        source: ./gcp-sa.json          # JSON placed next to the compose file (or use an absolute path)
        target: /app/creds/gcp-sa.json
        read_only: true
        bind:
          selinux: z                   # recommended on RHEL/Rocky/Fedora
    environment:
      GOOGLE_APPLICATION_CREDENTIALS: /app/creds/gcp-sa.json
      GOOGLE_CLOUD_PROJECT: your-project-id
```

Notes:
- If you run compose from the repo root with `-f deploy/docker/docker-compose-google-cloud-storage-connector.yaml`,
  `./gcp-sa.json` resolves relative to the file’s folder (`deploy/docker`). Place the JSON there or use an absolute path.
- On SELinux systems, use `:Z` (short syntax) or `bind.selinux: z` (long syntax) to label the bind mount.

Run:
```bash
docker compose -f deploy/docker/docker-compose-google-cloud-storage-connector.yaml up -d
```

Notes:
- If dsx-connect runs with HTTPS and a private CA, mount the CA and set:
  - `DSXCONNECTOR_VERIFY_TLS=true`
  - `DSXCONNECTOR_CA_BUNDLE=/app/certs/ca.crt`

## Rsync‑Like Filter Rules

See `shared/docs/filters.md` for details and examples of `DSXCONNECTOR_FILTER` patterns.
