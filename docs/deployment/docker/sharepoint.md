# SharePoint Connector — Docker Compose

This guide shows how to deploy the SharePoint connector with Docker Compose for quick testing/POV.

## Prerequisites
- Docker installed locally (or a container VM)
- SharePoint app registration credentials (tenant ID, client ID, client secret). See Reference → [Azure Credentials](../../reference/azure-credentials.md) for a step-by-step walkthrough.
- A Docker network shared with dsx‑connect (example: `dsx-connect-network`)

## Compose File
Use `connectors/sharepoint/deploy/docker/docker-compose-sharepoint-connector.yaml` as a starting point.

### Core connector env (common across connectors)

| Variable | Description |
| --- | --- |
| `DSXCONNECTOR_DSX_CONNECT_URL` | dsx-connect base URL (use `http://dsx-connect-api:8586` on the shared Docker network). |
| `DSXCONNECTOR_CONNECTOR_URL` | Callback URL dsx-connect uses to reach the connector (defaults to the service name inside the Docker network). |
| `DSXCONNECTOR_ASSET` | SharePoint scope, e.g., full site URL or doc library/folder path. |
| `DSXCONNECTOR_FILTER` | Optional rsync‑style include/exclude rules relative to the asset. |
| `DSXCONNECTOR_ITEM_ACTION` | What to do on malicious verdicts (`nothing`, `delete`, `move`, `move_tag`). Set to `move`/`move_tag` to relocate files after verdict. |
| `DSXCONNECTOR_ITEM_ACTION_MOVE_METAINFO` | Destination (site/doc lib/folder path or label) for moved items when using `move`/`move_tag`. |

### SharePoint-specific settings

Define these values in your Compose environment (the sample file expects plain `SP_*` variables in the shell/`.env` file; the compose template expands them to the connector-ready `DSXCONNECTOR_SP_*` envs).

| Variable | Description |
| --- | --- |
| `SP_TENANT_ID` | Azure AD tenant ID for the SharePoint app registration. |
| `SP_CLIENT_ID` | Client ID for the SharePoint app registration. |
| `SP_CLIENT_SECRET` | Client secret for the SharePoint app registration (store securely). |
| `SP_VERIFY_TLS` | Optional override (`true`/`false`) for Graph TLS verification (defaults to `true`). |
| `SP_CA_BUNDLE` | Optional CA bundle path for Graph TLS verification. |
| `SP_WEBHOOK_ENABLED` | Set to `true` to enable Microsoft Graph change notifications (optional). |
| `SP_WEBHOOK_URL` | Public HTTPS URL Graph calls for change notifications (required when webhooks enabled). |
| `SP_WEBHOOK_CLIENT_STATE` | Optional shared secret Graph includes in webhook payloads. |
| `SP_WEBHOOK_CHANGE_TYPES` | Optional override of Graph change types (default `updated`). |

Example `.env` fragment for Compose:

```bash
# SharePoint credentials
SP_TENANT_ID=d1509054-f881-493e-9d8e-e69932e4e865
SP_CLIENT_ID=2d546ee8-0592-4aa7-9d3e-1f03e398634c
SP_CLIENT_SECRET=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

# Optional overrides
SP_VERIFY_TLS=true
SP_CA_BUNDLE=

# Change notifications (optional)
SP_WEBHOOK_ENABLED=false
SP_WEBHOOK_URL=
SP_WEBHOOK_CLIENT_STATE=
```

Launch with:
```bash
docker compose --env-file .env \
  -f connectors/sharepoint/deploy/docker/docker-compose-sharepoint-connector.yaml up -d
```

## Assets and Filters
- `DSXCONNECTOR_ASSET` should be set to your SharePoint scope (site/doc lib/folder). Navigate to the exact folder in SharePoint Online, grab the full URL (e.g., `https://contoso.sharepoint.com/sites/Site/Shared%20Documents/dsx-connect/scantest`), and paste it here.
- Filters are evaluated relative to that scope (children).
- See Reference → [Assets & Filters](../../reference/assets-and-filters.md) for sharding/partition guidance.

## Notes
- Use `DSXCONNECTOR_ASSET` to configure the SharePoint URL scope (site/doc lib/folder).

## TLS Options
- `DSXCONNECTOR_USE_TLS`: Serve the connector over HTTPS (mount cert/key and enable as needed).
- `DSXCONNECTOR_TLS_CERTFILE` / `DSXCONNECTOR_TLS_KEYFILE`: Paths to the mounted certificate and key when TLS is enabled.
- `DSXCONNECTOR_VERIFY_TLS`: Keep `true` (default) to verify dsx-connect’s certificate; set to `false` only for local dev.
- `DSXCONNECTOR_CA_BUNDLE`: Optional CA bundle path when verifying dsx-connect with a private CA.

## Webhook Exposure
If you expose SharePoint webhook callbacks or other HTTP endpoints outside Docker, tunnel or publish the host port mapped to `8640` (compose default when ports are enabled). Keep `DSXCONNECTOR_CONNECTOR_URL` pointing to the Docker-network URL (e.g., `http://sharepoint-connector:8640`) so dsx-connect can reach the container internally.
