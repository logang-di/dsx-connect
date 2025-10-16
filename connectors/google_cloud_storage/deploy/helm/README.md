# Google Cloud Storage Connector Helm Chart

This chart deploys the DSX-Connect Google Cloud Storage Connector to Kubernetes.
It connects to a target GCS bucket/prefix and reports to `dsx-connect`.

## Prerequisites
- Kubernetes 1.19+
- Helm 3.2+
- `kubectl` configured for your cluster
- Google credentials via one of:
  - Workload Identity/GKE metadata (preferred in GKE) — no key file/Secret needed
  - Service Account JSON key (create a Kubernetes Secret and mount it)

---

## Create GCP Credentials (Service Account Key)

If you are not using Workload Identity, create a service account and key with the minimum roles required for your use case.

1) Create a service account

```bash
export PROJECT_ID=<your-project-id>
gcloud iam service-accounts create dsx-gcs-connector \
  --display-name "DSX Connect GCS Connector"
```

2) Grant roles (choose according to the actions you enable in the connector)

- Read-only (listing and scanning only):
  - roles/storage.objectViewer
- Tagging (object metadata updates):
  - roles/storage.objectUser (or grant `storage.objects.update` via a custom role)
- Move/Delete/Quarantine (copy+delete or write):
  - roles/storage.objectAdmin

```bash
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member "serviceAccount:dsx-gcs-connector@${PROJECT_ID}.iam.gserviceaccount.com" \
  --role roles/storage.objectViewer

# Add the following as needed for write operations
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member "serviceAccount:dsx-gcs-connector@${PROJECT_ID}.iam.gserviceaccount.com" \
  --role roles/storage.objectAdmin
```

3) Create a JSON key

```bash
gcloud iam service-accounts keys create sa.json \
  --iam-account dsx-gcs-connector@${PROJECT_ID}.iam.gserviceaccount.com
```

4) Simplest: create the Secret from the provided manifest

- Edit `gcp-sa-secret.yaml` in this directory and paste your key into `stringData.service-account.json`.
- Apply it to your target namespace (example uses `default`; change as needed):

```bash
kubectl -n default apply -f gcp-sa-secret.yaml
```

- Configure the chart to use that Secret (if your values don’t already set it):

```yaml
gcp:
  credentialsSecretName: gcp-sa   # must match metadata.name in gcp-sa-secret.yaml
  mountPath: /app/creds
  filename: service-account.json  # must match the key in the Secret
```

The chart mounts the Secret and sets `GOOGLE_APPLICATION_CREDENTIALS=/app/creds/service-account.json` for the pod.

Alternative (CLI): create the Secret without editing the file

```bash
kubectl -n default create secret generic gcp-sa \
  --from-file=service-account.json=./sa.json
```

---

---

## Quick Config Reference

- env.DSXCONNECTOR_ASSET: GCS bucket (optionally with a prefix). Example: `invoices-bucket` or `invoices-bucket/prefix`. When a prefix is provided, listings start at that sub-root and filters are evaluated relative to it.
- env.DSXCONNECTOR_FILTER: Optional include/exclude rules under the asset root. Use for prefix scoping too (e.g., `prefix/**`). Follows rsync‑like rules. Examples: `"prefix/**"`, "**/*.zip,**/*.docx", or "-tmp --exclude cache".
- env.DSXCONNECTOR_DISPLAY_NAME: Optional friendly name shown on the dsx-connect UI card (e.g., "Google Cloud Storage Connector").
- env.DSXCONNECTOR_ITEM_ACTION: What to do with malicious files. One of: `nothing` (default), `delete`, `tag`, `move`, `move_tag`.
- env.DSXCONNECTOR_ITEM_ACTION_MOVE_METAINFO: Target when action is `move` or `move_tag` (e.g., `dsxconnect-quarantine`).

These are the most commonly changed settings on first deploy.

FILTER (rsync‑like) quick cheat:
- `?` matches any single non-slash char; `*` matches 0+ non-slash; `**` matches 0+ including slashes.
- `-`/`--exclude` exclude rule; `+`/`--include` include rule; comma‑separate or space‑separate tokens.
 - See “Rsync‑Like Filter Rules” at the end of this document.

## Deployment Methods

This chart is flexible. The following methods show how to deploy it, from a simple test to a production-grade workflow.

### Method 1: Quick Start (Command-Line Overrides)
Install (ensure the GCP Secret exists, or use Workload Identity):

- Release name must be unique. Suggested: `gcs-<asset>-<env>` (e.g., `gcs-invoices-dev`).
- Specify the image version when installing from this chart path.
  - From local path: add `--set-string image.tag=<version>`
  - From OCI (Method 3): use `--version <version>` instead

```bash
helm install gcs-invoices-dev . \
  --set env.DSXCONNECTOR_ASSET=my-bucket/prefix1 \
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
helm install gcs oci://registry-1.docker.io/dsxconnect/google-cloud-storage-connector-chart \
  --version <ver> \
  --set env.DSXCONNECTOR_ASSET=my-bucket \
  --set-string env.DSXCONNECTOR_FILTER="**/*.zip,**/*.docx"
```

Note: OCI installs are prewired — the chart `--version` selects a chart whose `appVersion` becomes the default image tag. You can override with `--set-string image.tag=...`.

Secrets: Ensure the Google credentials Secret exists (or use Workload Identity) before installing.

### Using Workload Identity (GKE)

When running on GKE with Workload Identity, you do not need a key or Secret:

1) Create/annotate a Kubernetes ServiceAccount used by this chart’s Deployment.
2) Bind that KSA to the GCP service account (`dsx-gcs-connector@…`) with the required roles.
3) Omit `gcp.credentialsSecretName` so the chart does not mount a key or set `GOOGLE_APPLICATION_CREDENTIALS`.

Refer to Google’s docs: https://cloud.google.com/kubernetes-engine/docs/how-to/workload-identity

### Method 4: Production-Grade Deployment (GitOps & CI/CD)

Store environment-specific values files in a GitOps repo and sync this chart from the OCI registry with those values using Argo CD or Flux.

## Connecting to dsx-connect
By default, the chart computes `DSXCONNECTOR_DSX_CONNECT_URL`:
- `http://dsx-connect-api` when running without TLS
- `https://dsx-connect-api` when running with TLS
Override with `--set env.DSXCONNECTOR_DSX_CONNECT_URL=...` if `dsx-connect` is external.

## Verify
```bash
helm list
kubectl get pods
kubectl logs deploy/google-cloud-storage-connector -f
```

See `values.yaml` for all options.

## Write‑Enabled Example (Tag/Move)

If you enable actions like `tag`, `move`, `move_tag`, or `delete`, grant appropriate roles to the GCP service account and set the action in values.

Minimum roles:
- Tagging (metadata updates): include `storage.objects.update` (e.g., `roles/storage.objectUser`)
- Move/Delete/Quarantine: include `storage.objects.create`, `storage.objects.delete`, `storage.objects.get` (e.g., `roles/storage.objectAdmin`)

## Asset vs Filter

- Asset: Absolute base in the repository. No wildcards. For GCS, this is `bucket` or `bucket/prefix`. Listings start here and webhooks (if used) should be scoped here.
- Filter: Rsync‑like include/exclude rules relative to the asset base. Supports wildcards and exclusions.
- Equivalences:
  - `asset=my-bucket`, `filter=prefix1/**`  ≈  `asset=my-bucket/prefix1`, `filter=""`
  - `asset=my-bucket`, `filter=sub1`       ≈  `asset=my-bucket/sub1`, `filter=""` (common usage)
  - `asset=my-bucket`, `filter=sub1/*`     ≈  `asset=my-bucket/sub1`, `filter="*"`
- Guidance:
  - Prefer Asset for the stable, exact root (best provider `prefix` narrowing and clarity).
  - Use Filter for wildcard selection and excludes under that root.
  - Plain‑English note: “Complex” filters (e.g., excludes like `-tmp`, or advanced globs beyond simple directory includes) prevent tight provider‑side `prefix` narrowing. The connector will list more broadly (sometimes the whole bucket/prefix) and then match/exclude locally, which is slower than starting from an exact asset sub‑root.

Example values for a quarantining connector that moves files to a destination:

```yaml
gcp:
  credentialsSecretName: gcp-sa

env:
  DSXCONNECTOR_ASSET: "my-bucket"
  DSXCONNECTOR_FILTER: "**/*.zip,**/*.docx"
  DSXCONNECTOR_ITEM_ACTION: "move_tag"   # or "move" or "tag"
  DSXCONNECTOR_ITEM_ACTION_MOVE_METAINFO: "dsxconnect-quarantine"
  # Optional: TLS to dsx-connect
  # DSXCONNECTOR_VERIFY_TLS: "true"
  # DSXCONNECTOR_CA_BUNDLE: "/app/certs/ca/ca.crt"
```

Ensure the service account has the required permissions on the target buckets (source and quarantine destination, if different).

## TLS to dsx-connect (CA Bundle)

If dsx-connect runs with a private/internal CA, set:
- `env.DSXCONNECTOR_VERIFY_TLS=true`
- `env.DSXCONNECTOR_CA_BUNDLE=/app/certs/ca/ca.crt`
- Mount the CA secret and add volume/volumeMount in your values.

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

Examples (paths are relative to `DSXCONNECTOR_ASSET`):

| DSXCONNECTOR_FILTER                                   | Description                                                                 |
|-------------------------------------------------------|-----------------------------------------------------------------------------|
| ""                                                    | All files recursively (no filter)                                           |
| "*"                                                   | Only top‑level files (no recursion)                                         |
| "prefix/**"                                           | Everything under `prefix/` (common for “prefix” scoping)                    |
| "sub1"                                                | Files within subtree `sub1` (recurse into subtrees)                         |
| "sub1/*"                                              | Files directly under `sub1` (no recursion)                                  |
| "sub1/sub2"                                           | Files within subtree `sub1/sub2` (recurse)                                   |
| "*.zip,*.docx"                                        | All files with .zip and .docx extensions                                    |
| "-tmp --exclude cache"                                | Exclude `tmp` and `cache` directories                                       |
| "sub1 -tmp --exclude sub2"                            | Include `sub1` subtree but exclude `tmp` and `sub2`                         |
| "test/2025*/*"                                        | Files in subtrees matching `test/2025*/*` (no recursion)                    |
| "test/2025*/** -sub2"                                 | Recurse under `test/2025*/**`, excluding any `sub2` subtree                 |
| "'scan here' -'not here' --exclude 'not here either'" | Quoted tokens for names with spaces                                          |
## Sharding & Deployment Strategies

- Multiple instances: Run separate releases of the connector to split work across natural subtrees.
  - Asset-based sharding: set `DSXCONNECTOR_ASSET="bucket/prefix1/sub1"` for instance A, `bucket/prefix1/sub2"` for instance B. Use `DSXCONNECTOR_FILTER` only for fine-grained includes/excludes under that sub-root.
  - Filter-based sharding: set `DSXCONNECTOR_ASSET="bucket"` and use include-only filters per instance (e.g., `prefix1/sub1/**`, `prefix1/sub2/**`).
- Performance tip: Prefer include-only filters for provider-side prefix narrowing. Adding excludes is supported, but may require broader provider listing with client-side filtering.
- Examples (two shards):
  - A: `DSXCONNECTOR_ASSET=my-bucket/prefix1/sub1`, `DSXCONNECTOR_FILTER="**/*.zip"`
  - B: `DSXCONNECTOR_ASSET=my-bucket/prefix1/sub2`, `DSXCONNECTOR_FILTER="**/*.zip"`
