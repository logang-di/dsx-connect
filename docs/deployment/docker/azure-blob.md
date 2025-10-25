# Azure Blob Storage Connector — Docker Compose

This guide shows how to deploy the Azure Blob connector with Docker Compose for quick testing/POV.

## Prerequisites
- Docker installed locally (or a container VM)
- Azure Storage credentials with permissions to list/read (and optionally write/move/delete) blobs:
  - Connection string (recommended for POV) or SAS/Managed Identity as applicable
- A Docker network shared with dsx‑connect (example: `dsx-connect-network`)

## Compose File
Use `connectors/azure_blob_storage/deploy/docker/docker-compose-azure-blob-storage-connector.yaml` as a starting point.

### Core connector env (common across connectors)

| Variable | Description |
| --- | --- |
| `DSXCONNECTOR_DSX_CONNECT_URL` | dsx‑connect base URL (use `http://dsx-connect-api:8586` on the shared Docker network). |
| `DSXCONNECTOR_ASSET` | Container or `container/prefix` to scope listings. |
| `DSXCONNECTOR_FILTER` | Optional rsync‑style include/exclude rules relative to the asset. |
| `DSXCONNECTOR_ITEM_ACTION` | What to do on malicious verdicts (`nothing`, `delete`, `move`, `move_tag`). Use `move`/`move_tag` to relocate blobs after verdict. |
| `DSXCONNECTOR_ITEM_ACTION_MOVE_METAINFO` | Destination container/prefix for moved blobs when using `move`/`move_tag`. |

### Azure-specific settings

| Variable | Description |
| --- | --- |
| `AZURE_STORAGE_CONNECTION_STRING` | Connection string for the storage account (store via secrets). |

Example:
```bash
docker compose -f connectors/azure_blob_storage/deploy/docker/docker-compose-azure-blob-storage-connector.yaml up -d
```

## Assets and Filters
- `DSXCONNECTOR_ASSET` should be set to your container (e.g., `my-container`) or `container/prefix` to scope listings.
- If a prefix is provided, listings start at that sub‑root and filters are evaluated relative to it.
- See Reference → [Assets & Filters](../../reference/assets-and-filters.md) for sharding/partition guidance.

## Notes
- Provide `AZURE_STORAGE_CONNECTION_STRING` (or other supported auth env) via secrets for security.

## TLS Options
- `DSXCONNECTOR_USE_TLS`: Serve the connector over HTTPS (mount cert/key and enable as needed).
- `DSXCONNECTOR_TLS_CERTFILE` / `DSXCONNECTOR_TLS_KEYFILE`: Paths to the mounted certificate and key when TLS is enabled.
- `DSXCONNECTOR_VERIFY_TLS`: Keep `true` (default) to verify dsx-connect’s certificate; set to `false` only for local dev.
- `DSXCONNECTOR_CA_BUNDLE`: Optional CA bundle path when verifying dsx-connect with a private CA.

## Provider Notes (Azure Blob)
- Auth: connection string works well for POV; SAS or managed identity might be used in production.
- HNS (ADLS Gen2): hierarchical namespace affects path semantics; test your prefixes under HNS.
- Listing costs: large containers can incur list costs; sharding by asset improves performance.
- SAS Expiry: ensure long enough validity for ongoing scans.
