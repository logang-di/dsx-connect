# AWS S3 Connector — Docker Compose

This guide shows how to deploy the AWS S3 connector with Docker Compose for quick testing/POV.

## Prerequisites
- Docker installed locally (or a container VM)
- AWS credentials (as env vars or a secret) with permissions to list/read (and optionally write/move/delete) objects
- A Docker network shared with dsx‑connect (example: `dsx-connect-network`)

## Compose File
Use the example at `connectors/aws_s3/deploy/docker/docker-compose-aws-s3-connector.yaml` as a starting point.

### Core connector env (common across connectors)

| Variable | Description |
| --- | --- |
| `DSXCONNECTOR_DSX_CONNECT_URL` | dsx‑connect base URL (use `http://dsx-connect-api:8586` on the shared Docker network). |
| `DSXCONNECTOR_CONNECTOR_URL` | Callback URL dsx-connect uses to reach the connector (defaults to the service name inside the Docker network). |
| `DSXCONNECTOR_ASSET` | Target bucket or `bucket/prefix` to scope listings. |
| `DSXCONNECTOR_FILTER` | Optional rsync‑style include/exclude rules relative to the asset. |
| `DSXCONNECTOR_ITEM_ACTION` | What to do on malicious verdicts (`nothing`, `delete`, `move`, `move_tag`). Set to `move`/`move_tag` to trigger connector-side quarantine. |
| `DSXCONNECTOR_ITEM_ACTION_MOVE_METAINFO` | Destination bucket/prefix to receive moved objects when `item_action` is `move` or `move_tag`. |

### AWS-specific settings

| Variable | Description |
| --- | --- |
| `AWS_ACCESS_KEY_ID` | Access key with List/Get (and optional Put/Delete) permissions for the target bucket. |
| `AWS_SECRET_ACCESS_KEY` | Secret for the access key above. |

Example:
```bash
docker compose -f connectors/aws_s3/deploy/docker/docker-compose-aws-s3-connector.yaml up -d
```

## Assets and Filters
- `DSXCONNECTOR_ASSET` should be set to your bucket (e.g., `my-bucket`) or `bucket/prefix` to scope listings.
- If a prefix is provided, listings start at that sub‑root and filters are evaluated relative to it.
- See Reference → [Assets & Filters](../../reference/assets-and-filters.md) to understand sharding and partitioning patterns.

## Notes
- Use `DSXCONNECTOR_ASSET` to configure the target bucket or `bucket/prefix`.

## TLS Options
- `DSXCONNECTOR_USE_TLS`: Serve the connector over HTTPS (mount cert/key and set this to `true`).
- `DSXCONNECTOR_TLS_CERTFILE` / `DSXCONNECTOR_TLS_KEYFILE`: Paths to the mounted certificate and private key when TLS is enabled.
- `DSXCONNECTOR_VERIFY_TLS`: Keep `true` (default) to verify dsx-connect’s certificate; set to `false` only for local dev.
- `DSXCONNECTOR_CA_BUNDLE`: Optional CA bundle path when verifying dsx-connect with a private CA.

## Webhook Exposure
If you forward events into the connector’s HTTP endpoints (e.g., using tunnels or an external load balancer), expose the host port mapped to `8600` (default in compose) and point your upstream system at that URL. `DSXCONNECTOR_CONNECTOR_URL` should remain the Docker-network URL (e.g., `http://aws-s3-connector:8600`) so dsx-connect can reach the service internally.

## Provider Notes (AWS S3)
- Region/Endpoint: ensure the connector can reach the correct S3 endpoint for your bucket’s region.
- IAM Policies: least‑privilege for List/Get; add Put/Delete if actions are enabled.
- SSE‑KMS: if objects are KMS‑encrypted, confirm key permissions for decryption.
- Path‑style vs Virtual host: modern S3 endpoints default to virtual host; avoid path‑style unless required by your setup.
