# Google Cloud Storage Connector — Docker Compose

This guide shows how to deploy the Google Cloud Storage connector with Docker Compose for quick testing/POV.

## Prerequisites
- Docker installed locally (or a container VM)
- A GCP service account JSON secret (mounted into the container) with permissions to list/read (and optionally write/move/delete) objects
- A Docker network shared with dsx‑connect (example: `dsx-connect-network`)

## Compose File
Use `connectors/google_cloud_storage/deploy/docker/docker-compose-google-cloud-storage-connector.yaml` as a starting point.

### Core connector env (common across connectors)

| Variable | Description |
| --- | --- |
| `DSXCONNECTOR_DSX_CONNECT_URL` | dsx‑connect base URL (use `http://dsx-connect-api:8586` on the shared Docker network). |
| `DSXCONNECTOR_ASSET` | Target bucket or `bucket/prefix` to scope listings. |
| `DSXCONNECTOR_FILTER` | Optional rsync‑style include/exclude rules relative to the asset. |
| `DSXCONNECTOR_ITEM_ACTION` | What to do on malicious verdicts (`nothing`, `delete`, `move`, `move_tag`). Use `move`/`move_tag` to relocate objects after verdict. |
| `DSXCONNECTOR_ITEM_ACTION_MOVE_METAINFO` | Destination bucket/prefix for moved objects when using `move`/`move_tag`. |

### Google Cloud-specific settings

| Variable | Description |
| --- | --- |
| `GOOGLE_APPLICATION_CREDENTIALS` | Path to the mounted service account JSON (e.g., `/app/creds/service-account.json`). |

Example:
```bash
docker compose -f connectors/google_cloud_storage/deploy/docker/docker-compose-google-cloud-storage-connector.yaml up -d
```

## Assets and Filters
- `DSXCONNECTOR_ASSET` should be set to your bucket (e.g., `my-bucket`) or `bucket/prefix` to scope listings.
- If a prefix is provided, listings start at that sub‑root and filters are evaluated relative to it.
- See Reference → [Assets & Filters](../../reference/assets-and-filters.md) for sharding/partition guidance.

## Notes
- Use `DSXCONNECTOR_ASSET` to set the bucket (and optional prefix) to scan.

## TLS Options
- `DSXCONNECTOR_USE_TLS`: Serve the connector over HTTPS (mount cert/key as needed).
- `DSXCONNECTOR_TLS_CERTFILE` / `DSXCONNECTOR_TLS_KEYFILE`: Paths to the mounted certificate and key when TLS is enabled.
- `DSXCONNECTOR_VERIFY_TLS`: Keep `true` (default) to verify dsx-connect’s certificate; set to `false` only for local dev.
- `DSXCONNECTOR_CA_BUNDLE`: Optional CA bundle path when verifying dsx-connect with a private CA.
