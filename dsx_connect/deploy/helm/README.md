# DSX‑Connect Helm Chart

This Helm chart deploys the DSX‑Connect stack (API + workers + Redis + optional Syslog), with an optional in‑cluster DSXA scanner for local testing.

This guide explains the core configuration concepts and details three deployment methods, from a quick local test to a production-grade GitOps workflow.

## Prerequisites

- Kubernetes 1.19+ (a local cluster like Colima or Minikube is recommended for development).
- Helm 3.2+
- `kubectl` configured to point to your cluster.
- `openssl` for generating a self-signed certificate if you plan to enable TLS for development.

---

## Core Configuration Concepts

This umbrella chart deploys the DSX‑Connect stack. Key configuration areas (mirrors `values.yaml`):

1.  **global.env:** Minimal shared env (e.g., `DSXCONNECT_APP_ENV`, `DSXCONNECTOR_API_KEY`, scanner URL override).
2.  **global.image:** Optional image defaults inherited by subcharts.
3.  **global.scanner:** Hints for in‑cluster DSXA discovery (service name/port/scheme) when enabled.
4.  **dsx-connect-api:** API service and TLS settings; component‑specific env.
5.  **Worker charts:** Scan Request, Verdict Action, Results, Notification (env + Celery concurrency).
6.  **redis:** Message broker configuration (enabled by default).
7.  **syslog:** Optional Syslog service for results.
8.  **dsxa-scanner:** Optional single‑pod DSXA for local testing (disabled by default).

## Quick Reference (most deployments)

- `global.env.DSXCONNECT_SCANNER__SCAN_BINARY_URL`: REQUIRED when `dsxa-scanner.enabled=false` (default); set external DSXA endpoint.
- `dsx-connect-api.tls.enabled` + `dsx-connect-api.tls.secretName`: enable HTTPS for the API; certs mounted at `tls.mountPath`.  Will use self-signed certs if none supplied.
- `global.image.tag`: pin a specific image version across all components.
- `dsx-connect-*-worker.celery.concurrency`: adjust worker parallelism per queue.

---

## Deployment Methods

This chart is flexible. The following methods show how to deploy it, from a simple test to a production-grade workflow.

### Method 1: Quick Start (Command-Line Overrides)

This method is best for quick, temporary deployments, like for local testing. It uses the `--set` flag to provide configuration directly on the command line.

**1. Create the TLS Certificate Secret (if enabling TLS):**
   If you plan to enable TLS for the `dsx-connect-api` server, create your TLS secret first.
   ```bash
   kubectl create secret tls my-dsx-connect-api-tls --cert=tls.crt --key=tls.key
   ```

**2. Deploy the Stack:**
   *   **Simplest deployment: deploys DSXA scanner and dsx-connect on the same cluster:**
        Development mode deployment with a local DSXA scanner.  Use the `values-dev.yaml` (or copy it) to set deploy dsx-connect with a dsxa-scanner.  
        You can change the values in `values-dev.yaml` to match your environment,
        but, overriding values in the command line allows for flexible deployment.  In this case the only 
        setting that needs to be set is global.image.tag.
       ```bash 
       helm upgrade --install dsx . -f values-dev.yaml --set-string global.image.tag=0.2.82
       ```

   *   **Using an external DSX/A Scanner, HTTP deployment:**
        In this case, using the values.yaml (the default), DSXA scanner is not deployed, so the scan binary URL must be set. 
        You can either edit the values.yaml, or copy it and edit, or simply pass in settings on the helm arguments:
       ```bash
       helm upgrade --install dsx -f values.yaml --set-string global.image.tag=0.2.82
         --set-string global.env.DSXCONNECT_SCANNER__SCAN_BINARY_URL=http://my-dsxa-url:5000/scan/binary/v2
       ```

   *   **For a TLS-enabled deployment:**
       ```bash
       helm upgrade --install dsx . \
         --set-string
         --set dsx-connect-api.tls.enabled=true \
         --set dsx-connect-api.tls.secretName=my-dsx-connect-api-tls \
         --set-string global.env.DSXCONNECT_SCANNER__SCAN_BINARY_URL=https://my-dsxa.example.com/scan/binary/v2
       ```

### Production Install (recommended flags)

Use the production defaults in `values.yaml` and set the external DSXA URL explicitly. Also pin the image tag for reproducibility.

```bash
helm upgrade --install dsx dsx_connect/deploy/helm \
  -f dsx_connect/deploy/helm/values.yaml \
  --set-string global.env.DSXCONNECT_SCANNER__SCAN_BINARY_URL=https://my-dsxa.example.com/scan/binary/v2 \
  --set-string global.image.tag=0.2.66
```

### Method 2: Standard Deployment (Custom Values File)

This is the most common and recommended method for managing deployments. It involves creating a dedicated values file for each instance of the connector.

**1. Create the Required Secrets:**
   If enabling TLS for the API, create your TLS secret.
   ```bash
   kubectl create secret tls my-dsx-connect-api-tls --cert=tls.crt --key=tls.key
   ```

**2. Create a Custom Values File:**
   Create a new file, for example `my-dsx-connect-values.yaml`, to hold your configuration.

   ```yaml
   # my-dsx-connect-values.yaml

   global:
     env:
       DSXCONNECT_APP_ENV: prod
       DSXCONNECTOR_API_KEY: "your-prod-api-key"
       # REQUIRED when dsxa-scanner.enabled=false (default): point to your external DSXA
        DSXCONNECT_SCANNER__SCAN_BINARY_URL: "http://external-dsxa:5000/scan/binary/v2"
     scanner:
       # serviceName: "dsx-connect-dsxa-scanner"  # defaults to "<release>-dsxa-scanner"
       # port: 5000
       # scheme: http

   dsx-connect-api:
     tls:
       enabled: true
       secretName: "my-dsx-connect-api-tls"
     env:
       LOG_LEVEL: info

   dsx-connect-scan-request-worker:
     enabled: true
     env:
       LOG_LEVEL: info
     celery:
       concurrency: 2

   # ... configure other workers, redis, syslog as needed
   ```

**3. Install the Chart:**
   Install the chart, referencing your custom values file with the `-f` flag.
   ```bash
   helm install dsx-connect . -f my-dsx-connect-values.yaml
   ```

### Method 3: Production-Grade Deployment (GitOps & CI/CD)

This is the definitive, scalable, and secure approach for managing production applications. It uses modern Continuous Delivery (CD) mechanisms.

**The Philosophy:**
Instead of running `helm` commands manually, you declare the desired state of your application in a Git repository. A GitOps tool (like **Argo CD** or **Flux**) runs in your cluster, monitors the repository, and automatically syncs the cluster state to match what is defined in Git.

**The Workflow:**
This involves storing environment-specific values files (e.g., `values-prod.yaml`) in a separate GitOps repository. The GitOps tool then uses these files to automate Helm deployments, providing a fully auditable and declarative system for managing your application lifecycle.

---

## Packaging & Publishing (Helm)

You have a few good options to distribute this umbrella chart so others can install it with a single Helm command.

- Option A — OCI registry (recommended if you already push Docker images)
  - Package: `inv helm-package` (outputs `dist/charts/dsx-connect-<ver>.tgz`)
  - Login: `helm registry login registry-1.docker.io -u <user>`
  - Push: `inv helm-push-oci --repo=oci://registry-1.docker.io/dsxconnect`
  - Install: `helm install dsx-connect oci://registry-1.docker.io/dsxconnect/dsx-connect --version <ver> -f values.yaml`
  - Pros: lives alongside container images, easy auth, immutable versions.

### OCI Install (prewired image tag)

When installing from OCI with `--version X.Y.Z`, the chart at that version is pulled and its `appVersion` is used as the default image tag. In other words, versions are prewired — the Helm chart version selects the matching image tag by default. You can still override via `--set-string global.image.tag=...` if needed.

- Option B — Static Helm repo (e.g., GitHub Pages)
  - Package: `inv helm-package`
  - Index: `inv helm-repo-index --base-url=https://<org>.github.io/<repo>/charts`
  - Publish: upload contents of `dist/charts/` (including `index.yaml`) to your site (e.g., `gh-pages/charts/`)
  - Use: `helm repo add dsx https://<org>.github.io/<repo>/charts && helm install dsx-connect dsx/dsx-connect --version <ver>`
  - Pros: public/simple distribution, no auth required.

- Option C — Zip/attach chart in a release bundle
  - Package: `inv helm-package`; attach tgz to a GitHub release or bundle artifact.
  - Use: `helm install dsx-connect ./dsx-connect-<ver>.tgz`
  - Pros: lightweight for ad‑hoc distribution, but no repo metadata.

The Invoke tasks (`inv helm-package`, `inv helm-push-oci`, `inv helm-repo-index`) are provided to standardize the packaging flow. Choose the publishing method that best fits your environment and CI/CD.



## Advanced Configuration: Overriding Default Environment Variables

Many environment variables have sensible default values set directly within the subchart templates. These defaults align with the `docker-compose-dsx-connect-all-services.yaml` configuration. You only need to override them if your deployment requires a different value.

To override a default environment variable, specify it under the `env` section of the respective subchart in your custom `values.yaml` file.

**Commonly Overridden Variables (and their defaults):**

*   **`DSXCONNECT_APP_ENV`**: `dev` (used for Celery queue naming)
*   **`DSXCONNECTOR_API_KEY`**: `api-key-NOT-FOR-PRODUCTION`
*   **`DSXCONNECT_SCANNER__SCAN_BINARY_URL`**: REQUIRED when `dsxa-scanner.enabled=false` (the default). If you enable `dsxa-scanner`, templates compute `http(s)://<release>-dsxa-scanner:<port>/scan/binary/v2` using `global.scanner`.
*   **`DSXCONNECT_WORKERS__BROKER`**: `redis://redis:6379/5`
*   **`DSXCONNECT_WORKERS__BACKEND`**: `redis://redis:6379/6`
*   **`DSXCONNECT_REDIS_URL`**: `redis://redis:6379/3`
*   **`DSXCONNECT_RESULTS_DB`**: `redis://redis:6379/3`
*   **`DSXCONNECT_RESULTS_DB__RETAIN`**: `100`
*   Results/Stats DB: controlled by `DSXCONNECT_RESULTS_DB` (redis://… for Redis, else in-memory)
*   **`PYTHONUNBUFFERED`**: `1`
*   **`LOG_LEVEL`**: `debug` (for API), `warning` (for workers)

**Specific Worker Overrides:**

*   **`dsx-connect-results-worker`:**
    *   `DSXCONNECT_SCAN_RESULT_TASK_WORKER__SYSLOG_SERVER_URL`: `syslog`
    *   `DSXCONNECT_SCAN_RESULT_TASK_WORKER__SYSLOG_SERVER_PORT`: `514`

---


## Client Trust and CA Bundles

When clients (like the Azure Blob Storage Connector) communicate with the `dsx-connect-api` server over HTTPS, they must be able to verify the server's identity. If the `dsx-connect-api` server is using a certificate from an internal or self-signed Certificate Authority (CA), you must provide that CA's certificate to each client in a **CA Bundle**.

**Encryption vs. Authentication:**
It is important to understand that even with `verify=false`, the connection is still **encrypted**. However, without verification, the identity of the server is not **authenticated**. This leaves you vulnerable to man-in-the-middle attacks. **Using a CA bundle to verify the connection is critical for security.**

**Procedure for Clients (e.g., Azure Blob Storage Connector):**

1.  **Obtain the CA Certificate:** Get the public certificate file (e.g., `ca.crt`) of the CA that signed your `dsx-connect-api` server's certificate.

2.  **Create a Secret from the CA Certificate:**
    ```bash
    kubectl create secret generic dsx-connect-ca --from-file=ca.crt=/path/to/your/ca.crt
    ```

3.  **Configure the Client's Helm Chart:**
    Refer to the client's (e.g., `connectors/azure_blob_storage/deploy/helm/README.md`) documentation for how to configure its `DSXCONNECTOR_CA_BUNDLE` and `DSXCONNECTOR_VERIFY_TLS` settings to trust this CA.

---


## Verifying the Deployment

After deploying with any method, you can check the status of your release.

1.  **Check the Helm Release:**
    ```bash
    helm list
    ```

2.  **Check the Pods:**
    ```bash
    kubectl get pods
    ```

## Full Configuration Parameters

For a full list of configurable parameters for all subcharts, see the `values.yaml` file.

```
## Minimal configuration

For most deployments you only need to set a few environment values once, at the top‑level `values.yaml` under `global.env`:

```
global:
  env:
    DSXCONNECT_APP_ENV: dev
    DSXCONNECTOR_API_KEY: api-key-NOT-FOR-PRODUCTION
    # Optional: only set if pointing to an external DSXA; otherwise dsx-connect auto-points to the in-cluster DSXA service when enabled.
    # DSXCONNECT_SCANNER__SCAN_BINARY_URL: "http://<external-dsxa-host>:5000/scan/binary/v2"
```

All other settings have sensible defaults baked into the subcharts’ templates (Redis URLs, DB paths, etc.). You can still override per‑service `env` keys if needed, but typically the defaults are fine.

### Scanner Discovery (External vs In‑Cluster)

- Default (external DSXA): `dsxa-scanner.enabled=false` (values default). You must set `global.env.DSXCONNECT_SCANNER__SCAN_BINARY_URL` to your DSXA endpoint.
- In‑cluster (local testing): enable `dsxa-scanner.enabled=true`. API and workers default to `http(s)://<release>-dsxa-scanner:<port>/scan/binary/v2`, guided by `global.scanner` hints (service name/port/scheme). You can still override the env if needed.

Override examples:

- All components:
  - `--set-string global.env.DSXCONNECT_SCANNER__SCAN_BINARY_URL=http://external-dsxa:5000/scan/binary/v2`
- Only API:
  - `--set-string dsx-connect-api.env.DSXCONNECT_SCANNER__SCAN_BINARY_URL=http://external-dsxa:5000/scan/binary/v2`

## Optional: Example DSXA Scanner

For quick local testing, this chart can deploy a single DSXA scanner pod (not production). Enable it and DSX‑connect will default its scanner URL to that in‑cluster service name.

- Enable in `values.yaml`:
  ```yaml
  dsxa-scanner:
    enabled: true
  ```

- Or via CLI:
  ```bash
  helm upgrade --install dsx . -f values.yaml --set dsxa-scanner.enabled=true
  ```

When enabled, API and workers default to:
`DSXCONNECT_SCANNER__SCAN_BINARY_URL = http://<release>-dsxa-scanner:5000/scan/binary/v2`
You can still override this env if you’re pointing at an external DSXA.

## Image Version Overrides

How image tags are chosen:

- From local chart path (this repo): templates default to the chart `appVersion` unless you override `global.image.tag` (or per‑subchart `image.tag`).
- From OCI registry (helm install oci://… --version X.Y.Z): Helm pulls the chart at that version; templates use that chart’s `appVersion` as the default image tag. You can still override with `--set-string global.image.tag=...` if needed.

Override examples (no need to edit `values.yaml`):

- All components (via global image tag):
  - `helm upgrade --install dsx . -f values.yaml --set-string global.image.tag=0.2.66`
  - Later: `helm upgrade dsx . --reuse-values --set-string global.image.tag=0.2.67`

- Single component (per-subchart):
  - API only: `helm upgrade dsx . --reuse-values --set-string dsx-connect-api.image.tag=0.2.66`
  - Results worker: `helm upgrade dsx . --reuse-values --set-string dsx-connect-results-worker.image.tag=0.2.66`

- Quick hot-fix without Helm (not recommended long-term):
  - `kubectl set image deploy/dsx-connect-api dsx-connect-api=dsxconnect/dsx-connect:0.2.66`
  - `kubectl rollout status deploy/dsx-connect-api`

Tip: In CI, drive the tag with a variable, e.g.

```
TAG="$(git describe --tags --always)"
helm upgrade dsx . --reuse-values --set-string global.image.tag="$TAG"
```

---

## Appendix: GCP Connector Credentials and Docs

If you plan to deploy the Google Cloud Storage connector, you need credentials unless using GKE Workload Identity.

- Full guide: `connectors/google_cloud_storage/deploy/helm/README.md` (includes SA creation, roles, Secret mounting, and WI).

Quick summary (Service Account key method):

1) Create a GCP service account and grant minimum roles
   - Read-only scanning: `roles/storage.objectViewer`
   - Tag/Move/Delete (write ops): `roles/storage.objectAdmin` (or a tighter custom role with `storage.objects.update`, `storage.objects.create`, `storage.objects.delete`, `storage.objects.get`)

2) Create a key and Kubernetes Secret
   ```bash
   gcloud iam service-accounts keys create sa.json \
     --iam-account dsx-gcs-connector@${PROJECT_ID}.iam.gserviceaccount.com
   kubectl create secret generic gcp-sa --from-file=service-account.json=./sa.json
   ```

3) Set connector chart values
   ```yaml
   gcp:
     credentialsSecretName: gcp-sa
   env:
     DSXCONNECTOR_ASSET: your-bucket
   ```

For Workload Identity on GKE: omit `gcp.credentialsSecretName` and bind the Kubernetes ServiceAccount to the GCP SA with the required roles.
