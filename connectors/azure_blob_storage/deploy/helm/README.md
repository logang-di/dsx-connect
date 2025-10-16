# Azure Blob Storage Connector Helm Chart

This Helm chart provides a flexible and secure way to deploy the DSX-Connect Azure Blob Storage Connector to a Kubernetes cluster.

This guide explains the core configuration concepts and details three deployment methods, from a quick local test to a production-grade GitOps workflow.

## Prerequisites

- Kubernetes 1.19+ (a local cluster like Colima or Minikube is recommended for development).
- Helm 3.2+
- `kubectl` configured to point to your cluster.
- `openssl` for generating a self-signed certificate if you plan to enable TLS for development.
 - Azure Storage connection Secret — required for ALL install methods:
   - Option A (from chart directory): `kubectl apply -f azure-secret.yaml` (edit connection string first)
   - Option A (from monorepo): `kubectl apply -f connectors/azure_blob_storage/deploy/helm/azure-secret.yaml`
   - Option B (inline): `kubectl create secret generic azure-storage-connection-string --from-literal=AZURE_STORAGE_CONNECTION_STRING='<conn-string>'`

---

## Quick Config Reference

- env.DSXCONNECTOR_ASSET: Azure Blob container (optionally with a virtual folder/prefix). Example: `my-container` or `my-container/prefix`. When a prefix is provided, listings start at that sub-root and filters are evaluated relative to it.
- env.DSXCONNECTOR_FILTER: Optional include/exclude rules under the asset root. Use for prefix scoping too (e.g., `prefix/**`). Follows rsync‑like rules. Examples: `"prefix/**"`, "**/*.zip,**/*.docx", or "-tmp --exclude cache".
- env.DSXCONNECTOR_DISPLAY_NAME: Optional friendly name shown on the dsx-connect UI card (e.g., "Azure Blob Storage Connector").
- env.DSXCONNECTOR_ITEM_ACTION: What to do with malicious files. One of: `nothing` (default), `delete`, `tag`, `move`, `move_tag`.
- env.DSXCONNECTOR_ITEM_ACTION_MOVE_METAINFO: Target when action is `move` or `move_tag` (e.g., `dsxconnect-quarantine`).
- workers: Uvicorn worker processes per pod (default 1). Increases in-pod parallel request handling (e.g., read_file). Typical 2–4.
- replicaCount: Number of pods (default 1). Horizontal scaling and HA.

These are the most commonly changed settings on first deploy.

FILTER (rsync‑like) quick cheat:
- `?` matches any single non-slash char; `*` matches 0+ non-slash; `**` matches 0+ including slashes.
- `-`/`--exclude` exclude rule; `+`/`--include` include rule; comma‑separate or space‑separate tokens.
 - See “Rsync‑Like Filter Rules” at the end of this document.


## Deployment Methods

This chart is flexible. The following methods show how to deploy it, from a simple test to a production-grade workflow.

### Method 1: Quick Start (Command-Line Overrides)

This method is best for quick, temporary deployments, like for local testing. It uses the `--set` flag to provide configuration directly on the command line.

**1. Ensure the Azure Secret exists (see Prerequisites).**

**2. Deploy the Connector (Quick Start):**

- Release name must be unique. Suggested: `abs-<asset>-<env>` (e.g., `abs-invoices-dev`).
- Specify the image version when installing from this chart path.
  - From local path: add `--set-string image.tag=<version>`
  - From OCI (Method 3): use `--version <version>` instead

```bash
helm install abs-invoices-dev . \
  --set env.DSXCONNECTOR_ASSET=my-container \
  --set-string env.DSXCONNECTOR_FILTER="" \
  --set-string image.tag=<version>
```

### Method 2: Standard Deployment (Custom Values File)

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

Install directly from the OCI registry and set runtime configuration on the CLI (handy for quick tests and ephemeral installs).

```bash
helm install azure-abs oci://registry-1.docker.io/dsxconnect/azure-blob-storage-connector-chart \
  --version <ver> \
  --set env.DSXCONNECTOR_ASSET=my-container \
  --set-string env.DSXCONNECTOR_FILTER="*.zip,*.docx" \
  # optional TLS on the connector
  --set tls.enabled=true \
  --set tls.secretName=my-tls
```

Notes
- OCI install is “prewired”: the chart `--version` selects a chart with an `appVersion` that becomes the default image tag. You can still override with `--set-string image.tag=...`.

Secrets: Ensure the Azure Secret exists before installing.

### Method 4: Production-Grade Deployment (GitOps & CI/CD)

This is the definitive, scalable, and secure approach for managing production applications. It uses modern Continuous Delivery (CD) mechanisms.

**The Philosophy:**
Instead of running `helm` commands manually, you declare the desired state of your application in a Git repository. A GitOps tool (like **Argo CD** or **Flux**) runs in your cluster, monitors the repository, and automatically syncs the cluster state to match what is defined in Git.

**The Workflow:**
This involves storing environment-specific values files (e.g., `values-invoices-prod.yaml`) in a separate GitOps repository. The GitOps tool then uses these files to automate Helm deployments, providing a fully auditable and declarative system for managing your application lifecycle.

---

## Scaling and Workers

- `workers` controls the number of Uvicorn processes inside a single connector pod. Raise to 2–4 to increase parallel read_file handling without adding pods.
- `replicaCount` controls how many pods run behind the Service. Useful for horizontal scale and resiliency. Kubernetes balances requests across pods.
- Practical tips:
  - Favor modest Celery concurrency (2–4) on dsx-connect scan-request workers first; then add worker replicas when CPU-bound or for resiliency.
  - For this connector, raise `workers` to 2–4 if read_file is CPU-bound or you want more in-pod parallel reads; add replicas if a single pod’s CPU or network is saturated, or for HA.
  - If you see uneven distribution across replicas (HTTP keep-alive), higher Celery concurrency tends to open more connections and spread load better; httpx connection limits can be tuned later if needed.

Important:
- Each connector replica registers independently with dsx-connect and receives a unique connector UUID. You will see multiple connectors for the same asset/filter in the UI when `replicaCount > 1`.
- A Full Scan request targets one connector instance; increasing `replicaCount` does not parallelize a single Full Scan’s file enumeration. Prefer increasing `workers` (and dsx-connect worker concurrency) for throughput. Replicas primarily help HA and serving concurrent `read_file` requests; Service load-balancing spreads connections across pods.

Important:
- Each connector replica registers independently with dsx-connect and receives a unique connector UUID. You will see multiple connectors for the same asset/filter in the UI when `replicaCount > 1`.
- A Full Scan request targets one connector instance; increasing `replicaCount` does not parallelize a single Full Scan enqueue path. Prefer increasing `workers` (and dsx-connect worker concurrency) for throughput, and use replicas primarily for HA and serving concurrent `read_file` requests.

## Advanced Configuration

### Connecting to a `dsx-connect` Server with an Internal or Self-Signed Certificate

When your connector communicates with the main `dsx-connect` server over HTTPS, it must be able to verify the server's identity. If the `dsx-connect` server is using a certificate from an internal or self-signed Certificate Authority (CA), you must provide that CA's certificate to the connector in a **CA Bundle**.

**Encryption vs. Authentication:**
It is important to understand that even with `verify=false`, the connection is still **encrypted**. However, without verification, the identity of the server is not **authenticated**. This leaves you vulnerable to man-in-the-middle attacks. **Using a CA bundle to verify the connection is critical for security.**

**Procedure:**

1.  **Create a Secret from the CA Certificate:**
    First, obtain the public certificate file (e.g., `ca.crt`) of the CA that signed your `dsx-connect` server's certificate. Then, create a Kubernetes secret from it:
    ```bash
    kubectl create secret generic dsx-connect-ca --from-file=ca.crt=/path/to/your/ca.crt
    ```

2.  **Mount the CA Secret in `values.yaml`:**
    This chart does not have a dedicated value for the CA bundle secret. You must add the volume and volume mount definitions directly to your custom values file.

    ```yaml
    # my-connector-values.yaml

    # Add the volume for the CA secret
    volumes:
      - name: dsx-connect-ca-volume
        secret:
          secretName: dsx-connect-ca

    # Add the volume mount to the container
    volumeMounts:
      - name: dsx-connect-ca-volume
        mountPath: /app/certs/ca
        readOnly: true

    env:
      DSXCONNECTOR_ASSET: "my-production-container"
      # Tell the connector to verify the server
      DSXCONNECTOR_VERIFY_TLS: "true"
      # Point to the mounted CA bundle file
      DSXCONNECTOR_CA_BUNDLE: "/app/certs/ca/ca.crt"

    tls:
      enabled: true
      secretName: "my-tls"
    ```

3.  **Deploy the Chart:**
    Install the chart with your updated values file. The connector will now securely verify the `dsx-connect` server using the provided CA bundle.

---

## Verifying the Deployment

After deploying with any method, you can check the status of your release.

1.  **Check the Helm Release:**
    ```bash
    helm list
    ```

2.  **Check the Pod Status:**
    ```bash
    kubectl get pods
    ```

## Full Configuration Parameters

For a full list of configurable parameters, see the `values.yaml` file.



## Image Version Overrides

How image tags are chosen:

- From local chart path (this repo): templates default to the chart `appVersion` unless you override `image.tag`.
- From OCI registry (`helm install oci://… --version X.Y.Z`): Helm pulls the chart at that version; templates use that chart’s `appVersion` as the default image tag. You can still override with `--set-string image.tag=...` if needed.

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

- Asset: Absolute base in the repository. No wildcards. For Azure Blob, this is `container` or `container/prefix`. Listings start here and webhooks are scoped here.
- Filter: Rsync‑like include/exclude rules relative to the asset base. Supports wildcards and exclusions.
- Equivalences:
  - `asset=my-container`, `filter=prefix1/**`  ≈  `asset=my-container/prefix1`, `filter=""`
  - `asset=my-container`, `filter=sub1`       ≈  `asset=my-container/sub1`, `filter=""` (common usage)
  - `asset=my-container`, `filter=sub1/*`     ≈  `asset=my-container/sub1`, `filter="*"`
- When to choose which:
  - Prefer Asset for the stable, exact root of a scan (fast provider name_starts_with narrowing and simpler mental model).
  - Use Filter for wildcard selection and excludes under that root.
  - Plain‑English note: If your filter is “complex” (e.g., contains excludes like `-tmp`, or advanced globs beyond simple directory includes), the connector can’t rely on a tight `name_starts_with` prefix at the service. It will list more broadly (sometimes the whole container/prefix) and then match/exclude locally, which is slower than starting from an exact asset sub‑root.

## Sharding & Deployment Strategies

- Multiple instances: Deploy separate releases to split work across subtrees.
  - Asset-based sharding: `DSXCONNECTOR_ASSET="container/prefix1/sub1"` and `container/prefix1/sub2` per instance.
  - Filter-based sharding: `DSXCONNECTOR_ASSET=container` with include-only filters like `prefix1/sub1/**`, `prefix1/sub2/**`.
- Prefer include-only filters for provider-side `name_starts_with` narrowing. Excludes are supported, but may widen listings with client-side filtering.
- Example (two shards):
  - A: `DSXCONNECTOR_ASSET=my-container/prefix1/sub1`, `DSXCONNECTOR_FILTER="**/*.zip"`
  - B: `DSXCONNECTOR_ASSET=my-container/prefix1/sub2`, `DSXCONNECTOR_FILTER="**/*.zip"`
