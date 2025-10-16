# AWS S3 Connector Helm Chart

This chart deploys the DSX-Connect AWS S3 Connector to Kubernetes.
Use it to run one or more connector instances that watch a specific S3 bucket/prefix and communicate with `dsx-connect`.

## Prerequisites
- Kubernetes 1.19+
- Helm 3.2+
- `kubectl` configured for your cluster
- AWS credentials Secret — required for ALL install methods:
  - Option A (from chart directory): `kubectl apply -f aws-secret.yaml` (edit values first)
  - Option A (from monorepo): `kubectl apply -f connectors/aws_s3/deploy/helm/aws-secret.yaml`
  - Option B (inline): `kubectl create secret generic aws-credentials --from-literal=AWS_ACCESS_KEY_ID=... --from-literal=AWS_SECRET_ACCESS_KEY=...`

---

## Quick Config Reference

- env.DSXCONNECTOR_ASSET: S3 bucket (optionally with a prefix). Example: `invoices-bucket` or `invoices-bucket/prefix`. When a prefix is provided, listings start at that sub-root and filters are evaluated relative to it.
- env.DSXCONNECTOR_FILTER: Optional include/exclude rules under the asset root. Use for prefix scoping too (e.g., `prefix/**`). Follows rsync‑like rules. Examples: `"prefix/**"`, `"**/*.zip,**/*.docx"`, `"-tmp --exclude cache"`.
- env.DSXCONNECTOR_DISPLAY_NAME: Optional friendly name shown on the dsx-connect UI card (e.g., "AWS S3 Connector").
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
Install (ensure the AWS Secret exists; see Prerequisites):

- Release name must be unique. Suggested: `aws-<asset>-<env>` (e.g., `aws-invoices-dev`).
- Specify the image version when installing from this chart path.
  - From local path: add `--set-string image.tag=<version>`
  - From OCI (Method 3): use `--version <version>` instead

```bash
helm install aws-invoices-dev . \
  --set env.DSXCONNECTOR_ASSET=my-bucket \
  --set-string env.DSXCONNECTOR_FILTER="" \
  --set-string image.tag=<version>
```
Note: You can set other env keys as needed; most installs only adjust ASSET/FILTER.

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

Install from OCI and set the required runtime env on the CLI:

```bash
helm install aws-s3 oci://registry-1.docker.io/dsxconnect/aws-s3-connector-chart \
  --version <ver> \
  --set env.DSXCONNECTOR_ASSET=my-bucket \
  --set-string env.DSXCONNECTOR_FILTER="**/*.zip,**/*.docx"
```

Note: with OCI, the chart `--version` is prewired to the default image tag via the chart’s `appVersion`. Override with `--set-string image.tag=...` if needed.

Secrets: Ensure the AWS credentials Secret exists before installing.

### Method 4: Production-Grade Deployment (GitOps & CI/CD)

Store environment-specific `values-*.yaml` in a GitOps repo and let a controller (Argo CD, Flux) sync this chart from the OCI registry with your values. This provides declarative, auditable deployments.

## Connecting to dsx-connect
- By default, the chart computes `DSXCONNECTOR_DSX_CONNECT_URL` as:
  - `http://dsx-connect-api` when the connector runs without TLS
  - `https://dsx-connect-api` when the connector runs with TLS
- Override via `--set env.DSXCONNECTOR_DSX_CONNECT_URL=https://my-dsx.example.com` if dsx-connect is external.

## Verify
```bash
helm list
kubectl get pods
kubectl logs deploy/aws-s3-connector -f
```

For all options, see `values.yaml`.

## TLS to dsx-connect (CA Bundle)

If dsx-connect serves HTTPS with a private/internal CA, configure:
- `env.DSXCONNECTOR_VERIFY_TLS=true`
- `env.DSXCONNECTOR_CA_BUNDLE=/app/certs/ca/ca.crt`
- Mount the CA as a secret and add a volume/volumeMount in your values file.

## Image Version Overrides

- Local chart (this repo): the default image tag comes from the chart `appVersion` unless you override it. Override globally with `--set-string image.tag=<version>` or per chart value.
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

## Asset vs Filter

- Asset: Absolute base in the repository. No wildcards. For S3, this is `bucket` or `bucket/prefix`. Listings start here and webhooks are scoped here.
- Filter: Rsync‑like include/exclude rules relative to the asset base. Supports wildcards and exclusions.
- Equivalences:
  - `asset=my-bucket`, `filter=prefix1/**`  ≈  `asset=my-bucket/prefix1`, `filter=""`
  - `asset=my-bucket`, `filter=sub1`       ≈  `asset=my-bucket/sub1`, `filter=""` (common usage)
  - `asset=my-bucket`, `filter=sub1/*`     ≈  `asset=my-bucket/sub1`, `filter="*"`
- When to choose which:
  - Prefer Asset for the stable, exact root of a scan (fast provider prefix narrowing and simpler mental model).
  - Use Filter for wildcard selection and excludes under that root.
  - Plain‑English note: If your filter is “complex” (e.g., contains excludes like `-tmp`, or advanced globs beyond simple directory includes), the connector cannot rely on a tight Prefix at the provider. It will list a broader set of objects (sometimes the whole bucket/prefix) and then match/exclude locally, which is slower than starting at an exact asset sub‑root.

## Sharding & Deployment Strategies

- Multiple instances: Run separate releases of the connector to split work across natural subtrees.
  - Asset-based sharding: set `DSXCONNECTOR_ASSET="bucket/prefix1/sub1"` for instance A, `bucket/prefix1/sub2` for instance B. Use `DSXCONNECTOR_FILTER` only for fine-grained includes/excludes under that sub-root.
  - Filter-based sharding: set `DSXCONNECTOR_ASSET="bucket"` and use include-only filters per instance (e.g., `prefix1/sub1/**`, `prefix1/sub2/**`).
- Performance tip: Prefer include-only filters for provider-side prefix narrowing. Adding excludes is supported, but may require broader provider listing with client-side filtering.
- Examples (two shards):
  - A: `DSXCONNECTOR_ASSET=my-bucket/prefix1/sub1`, `DSXCONNECTOR_FILTER="**/*.zip"`
  - B: `DSXCONNECTOR_ASSET=my-bucket/prefix1/sub2`, `DSXCONNECTOR_FILTER="**/*.zip"`
  Or
  - A: `DSXCONNECTOR_ASSET=my-bucket`, `DSXCONNECTOR_FILTER="prefix1/sub1/**"`
  - B: `DSXCONNECTOR_ASSET=my-bucket`, `DSXCONNECTOR_FILTER="prefix1/sub2/**"`
