# SharePoint Connector Helm Chart

This chart deploys the DSX-Connect SharePoint connector.

## Prerequisites

- Kubernetes 1.19+
- Helm 3.2+
- `kubectl` configured for your cluster
- Secret with Microsoft Entra (Azure AD) app credentials — required for ALL install methods:
  - Option A (from chart directory): `kubectl apply -f sp-secret.yaml` (edit values first)
  - Option A (from monorepo): `kubectl apply -f connectors/sharepoint/deploy/helm/sp-secret.yaml`
  - Option B (inline): `kubectl create secret generic sharepoint-credentials \\
      --from-literal=DSXCONNECTOR_SP_TENANT_ID=<tenant-id> \\
      --from-literal=DSXCONNECTOR_SP_CLIENT_ID=<client-id> \\
      --from-literal=DSXCONNECTOR_SP_CLIENT_SECRET=<client-secret>`

## Deployment Methods

This chart is flexible. The following methods show how to deploy it, from a simple test to a production-grade workflow.

### Method 1: Quick Start (Command-Line Overrides)

1) Install the chart (ensure the Secret exists; see Prerequisites):

- Release name must be unique. Suggested: `sp-<asset>-<env>` (e.g., `sp-docs-dev`).
- Specify the image version when installing from this chart path.
  - From local path: add `--set-string image.tag=<version>`
  - From OCI (Method 3): use `--version <version>` instead

```bash
helm upgrade --install sp-docs-dev connectors/sharepoint/deploy/helm \
  --set env.DSXCONNECTOR_ASSET="https://<host>/sites/<SiteName>/Shared%20Documents" \
  --set-string env.DSXCONNECTOR_FILTER="" \
  --set-string image.tag=<version>
```

### Method 2: Standard Deployment (values file)

Using the values.yaml file for deployment configuration involves creating a dedicated values file for each instance of the connector.  Typically you shouldn't edit the values.yaml directly, but rather make a copy which represents each instance of the connector you
want to deploy.

For example, you can create a values file for each unique instance of the connector you want to deploy, such as `values-<env>-<asset>.yaml`,
i.e. `values-dev-my-asset1.yaml` or `values-prod-my-asset2.yaml`.

**1. Create a Custom Values File:**
Create a new file, for example `values-dev-my-asset1.yaml`, to hold your configuration.

   ```yaml
   # values-dev-my-asset1.yaml
...
   # Set the target asset for this connector instance
   env:
     DSXCONNECTOR_ASSET: "my-asset"
     DSXCONNECTOR_FILTER: "prefix/**"
...
   # Enable TLS and specify the secret to use
   tls:
     enabled: true
     secretName: "my-tls"
   ```

**2. Install the Chart:**
Install the chart, referencing your custom values file with the `-f` flag.
```bash
helm install my-connector . -f values-dev-my-asset1.yaml
```


### Method 3: OCI Repository + Command-Line Overrides

```bash
helm install sharepoint oci://registry-1.docker.io/dsxconnect/sharepoint-connector-chart \
  --version <ver> \
  --set env.DSXCONNECTOR_ASSET="https://<host>/sites/<SiteName>/Shared%20Documents" \
  --set-string env.DSXCONNECTOR_FILTER="**/*.zip,**/*.docx"
```

Note: OCI installs are prewired — the chart `--version` selects a chart whose `appVersion` becomes the default image tag. You can override with `--set-string image.tag=...`.

### Method 4: Production-Grade Deployment (GitOps & CI/CD)

Store environment-specific values in a GitOps repo and sync this chart from OCI using Argo CD or Flux.

## Values

- image.repository: Docker image repo (default dsxconnect/sharepoint-connector)
- image.tag: Image tag (default: Chart appVersion)
- service.port: Container port (80 unless TLS enabled)
- tls.enabled: Enable HTTPS for connector
- tls.secretName: Secret with `tls.crt` and `tls.key`
- env.*: Additional environment variables
- secrets.name: Name of Secret with SP credentials
- workers: Uvicorn worker processes per pod (default 1). Increases in-pod parallel request handling (e.g., read_file). Typical 2–4.
- replicaCount: Number of pods (default 1). Horizontal scaling and HA.

### Scaling and Workers

- `workers` controls the number of Uvicorn processes inside a single connector pod. Raise to 2–4 to increase parallel read_file handling without adding pods.
- `replicaCount` controls how many pods run behind the Service. Useful for HA and for capacity to serve concurrent `read_file` requests. Kubernetes balances connections across pods.
- Important: each replica registers as an independent connector with a unique UUID (you will see multiple connectors for the same asset/filter in the UI). A Full Scan request targets a single connector instance and its file enumeration is not parallelized by `replicaCount`. Replicas can still serve concurrent `read_file` requests initiated by dsx-connect workers (including those from a Full Scan) via Service load-balancing.
- Practical tips:
  - Favor modest Celery concurrency (2–4) on dsx-connect scan-request workers first; then add worker replicas when CPU-bound or for resiliency.
  - For this connector, raise `workers` to 2–4 if read_file is CPU-bound or you want more in-pod parallel reads; add replicas if a single pod’s CPU or network is saturated, or for HA.
  - If you see uneven distribution across replicas (HTTP keep-alive), higher Celery concurrency tends to open more connections and spread load better; httpx connection limits can be tuned later if needed.

The following envs are commonly set:

- env.DSXCONNECTOR_ASSET: SharePoint URL (library or folder)
- env.DSXCONNECTOR_FILTER: Optional filter expression

## Quick Config Reference

- env.DSXCONNECTOR_ASSET: SharePoint library or folder URL to scan. Example: `https://<host>/sites/<SiteName>/Shared%20Documents/dsx/prefix`.
- env.DSXCONNECTOR_FILTER: Optional include/exclude rules under the resolved base path. Use to scope into subfolders (e.g., `subdir/**`). Follows rsync‑like rules. Examples: "subdir/**", "**/*.zip,**/*.docx", "-tmp --exclude cache".
- env.DSXCONNECTOR_DISPLAY_NAME: Optional friendly name shown on the dsx-connect UI card (e.g., "SharePoint Connector").
- env.DSXCONNECTOR_ITEM_ACTION: What to do with malicious files. One of: `nothing` (default), `delete`, `tag`, `move`, `move_tag`.
- env.DSXCONNECTOR_ITEM_ACTION_MOVE_METAINFO: Target when action is `move` or `move_tag` (e.g., a quarantine folder path or label interpretable by the connector).

These are the most commonly changed settings on first deploy.

FILTER (rsync‑like) quick cheat:
- `?` matches any single non-slash char; `*` matches 0+ non-slash; `**` matches 0+ including slashes.
- `-`/`--exclude` exclude rule; `+`/`--include` include rule; comma‑separate or space‑separate tokens.
 - See “Rsync‑Like Filter Rules” at the end of this document.


## TLS to dsx-connect

Set `env.DSXCONNECTOR_VERIFY_TLS=true` and mount a CA bundle at `env.DSXCONNECTOR_CA_BUNDLE` if your dsx-connect uses a custom CA.

## Example

values.yaml override:

image:
  tag: "0.2.66"
env:
  DSXCONNECTOR_ASSET: "https://contoso.sharepoint.com/sites/Site/Shared%20Documents"

Install:

helm upgrade --install sharepoint connectors/sharepoint/deploy/helm -f values.yaml

## OCI Install (CLI overrides)

```bash
helm install sharepoint oci://registry-1.docker.io/dsxconnect/sharepoint-connector-chart \
  --version <ver> \
  --set env.DSXCONNECTOR_ASSET="https://<host>/sites/<SiteName>/Shared%20Documents" \
  --set-string env.DSXCONNECTOR_FILTER="**/*.zip,**/*.docx"
```

Note: OCI installs are prewired — the chart `--version` selects a chart whose `appVersion` becomes the default image tag. You can override with `--set-string image.tag=...`.

## Image Version Overrides

- Local chart (this repo): the default image tag comes from the chart `appVersion` unless you override it (e.g., `--set-string image.tag=<version>`).
- OCI install (e.g., `helm install oci://… --version X.Y.Z`): the chart at that version is pulled and its `appVersion` becomes the default image tag. You can still override with `--set-string image.tag=...`.

## Rsync‑Like Filter Rules

The `DSXCONNECTOR_FILTER` follows rsync include/exclude semantics. Leave empty ("") to scan everything under `DSXCONNECTOR_ASSET`.

- `?` matches any single character except a slash (/)
- `*` matches zero or more non‑slash characters
- `**` matches zero or more characters, including slashes
- `-` / `--exclude` exclude the following match
- `+` / `--include` include the following match
- Tokens can be comma‑separated or space‑separated; quote tokens that contain spaces

Examples (paths are relative to the resolved base path of `DSXCONNECTOR_ASSET`):

| DSXCONNECTOR_FILTER                                   | Description                                                                 |
|-------------------------------------------------------|-----------------------------------------------------------------------------|
| ""                                                    | All files recursively (no filter)                                           |
| "*"                                                   | Only top‑level files (no recursion)                                         |
| "prefix/**"                                           | Everything under `prefix/` in the selected library                          |
| "sub1"                                                | Files within subtree `sub1` (recurse into subtrees)                         |
| "sub1/*"                                              | Files directly under `sub1` (no recursion)                                  |
| "sub1/sub2"                                           | Files within subtree `sub1/sub2` (recurse)                                   |
| "*.zip,*.docx"                                        | All files with .zip and .docx extensions                                    |
| "-tmp --exclude cache"                                | Exclude `tmp` and `cache` directories                                       |
| "sub1 -tmp --exclude sub2"                            | Include `sub1` subtree but exclude `tmp` and `sub2`                         |
| "'scan here' -'not here' --exclude 'not here either'" | Quoted tokens for names with spaces                                          |
