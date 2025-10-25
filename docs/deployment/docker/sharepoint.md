# SharePoint Connector — Docker Compose

This guide shows how to deploy the SharePoint connector with Docker Compose for quick testing/POV.

## Prerequisites
- Docker installed locally (or a container VM)
- SharePoint App registration credentials (tenant ID, client ID, client secret)
- A Docker network shared with dsx‑connect (example: `dsx-connect-network`)

## Compose File
Use `connectors/sharepoint/deploy/docker/docker-compose-sharepoint-connector.yaml` as a starting point.

### Core connector env (common across connectors)

| Variable | Description |
| --- | --- |
| `DSXCONNECTOR_DSX_CONNECT_URL` | dsx‑connect base URL (use `http://dsx-connect-api:8586` on the shared Docker network). |
| `DSXCONNECTOR_ASSET` | SharePoint scope, e.g., full site URL or doc library/folder path. |
| `DSXCONNECTOR_FILTER` | Optional rsync‑style include/exclude rules relative to the asset. |
| `DSXCONNECTOR_ITEM_ACTION` | What to do on malicious verdicts (`nothing`, `delete`, `move`, `move_tag`). Set to `move`/`move_tag` to relocate files after verdict. |
| `DSXCONNECTOR_ITEM_ACTION_MOVE_METAINFO` | Destination (site/doc lib/folder path or label) for moved items when using `move`/`move_tag`. |

### SharePoint-specific settings

Define these env vars as `DSXCONNECTOR_<suffix>` in Compose — only the suffixes are shown below.

| Suffix | Description |
| --- | --- |
| `SP_TENANT_ID` | Azure AD tenant ID for the SharePoint app registration. |
| `SP_CLIENT_ID` | Client ID for the SharePoint app registration. |
| `SP_CLIENT_SECRET` | Client secret for the SharePoint app registration (store securely). |

Example:
```bash
docker compose -f connectors/sharepoint/deploy/docker/docker-compose-sharepoint-connector.yaml up -d
```

## Assets and Filters
- `DSXCONNECTOR_ASSET` should be set to your SharePoint scope (site/doc lib/folder).
- Filters are evaluated relative to that scope (children).
- See Reference → [Assets & Filters](../../reference/assets-and-filters.md) for sharding/partition guidance.

## Notes
- Use `DSXCONNECTOR_ASSET` to configure the SharePoint URL scope (site/doc lib/folder).

## TLS Options
- `DSXCONNECTOR_USE_TLS`: Serve the connector over HTTPS (mount cert/key and enable as needed).
- `DSXCONNECTOR_TLS_CERTFILE` / `DSXCONNECTOR_TLS_KEYFILE`: Paths to the mounted certificate and key when TLS is enabled.
- `DSXCONNECTOR_VERIFY_TLS`: Keep `true` (default) to verify dsx-connect’s certificate; set to `false` only for local dev.
- `DSXCONNECTOR_CA_BUNDLE`: Optional CA bundle path when verifying dsx-connect with a private CA.
