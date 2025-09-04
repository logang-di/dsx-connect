# DSX-Connect Helm Chart

This Helm chart provides a flexible and secure way to deploy the DSX-Connect Azure Blob Storage Connector to a Kubernetes cluster.

This guide explains the core configuration concepts and details three deployment methods, from a quick local test to a production-grade GitOps workflow.

## Prerequisites

- Kubernetes 1.19+ (a local cluster like Colima or Minikube is recommended for development).
- Helm 3.2+
- `kubectl` configured to point to your cluster.
- `openssl` for generating a self-signed certificate if you plan to enable TLS for development.

---

## Core Configuration Concepts

This umbrella chart deploys the entire DSX-Connect stack. Key configuration areas include:

1.  **Global Environment Variables:** Common environment variables shared across most DSX-Connect components (e.g., Redis URLs, database settings, API key).
2.  **API Server (dsx-connect-api):** Configuration for the main FastAPI server, including TLS settings.
3.  **Worker Services:** Configuration for the individual Celery worker types (Scan Request, Verdict Action, Results, Notification).
4.  **Redis:** Configuration for the Redis message broker.
5.  **Syslog:** Configuration for the optional Syslog server.

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

   *   **For a simple HTTP deployment:**
       ```bash
       helm install dsx-connect . \
         --set dsx-connect-api.env.LOG_LEVEL=debug \
         --set global.env.DSXCONNECT_APP_ENV=dev
       ```

   *   **For a TLS-enabled deployment:**
       ```bash
       helm install dsx-connect . \
         --set dsx-connect-api.tls.enabled=true \
         --set dsx-connect-api.tls.secretName=my-dsx-connect-api-tls \
         --set dsx-connect-api.env.LOG_LEVEL=info \
         --set global.env.DSXCONNECT_APP_ENV=prod
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

   # Global settings (optional, passed down if subcharts use .Values.global)
   global:
     logLevel: info
     env:
       DSXCONNECT_APP_ENV: prod
       DSXCONNECTOR_API_KEY: "your-prod-api-key"
       DSXCONNECT_SCANNER__SCAN_BINARY_URL: "http://dsxa-scanner:5000/scan/binary/v2"

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


## Advanced Configuration: Overriding Default Environment Variables

Many environment variables have sensible default values set directly within the subchart templates. These defaults align with the `docker-compose-dsx-connect-all-services.yaml` configuration. You only need to override them if your deployment requires a different value.

To override a default environment variable, specify it under the `env` section of the respective subchart in your custom `values.yaml` file.

**Commonly Overridden Variables (and their defaults):**

*   **`DSXCONNECT_APP_ENV`**: `dev` (used for Celery queue naming)
*   **`DSXCONNECTOR_API_KEY`**: `api-key-NOT-FOR-PRODUCTION`
*   **`DSXCONNECT_SCANNER__SCAN_BINARY_URL`**: defaults to `http://<release>-dsxa-scanner:5000/scan/binary/v2` when the bundled DSXA subchart is enabled; otherwise set this to your external DSXA.
*   **`DSXCONNECT_WORKERS__BROKER`**: `redis://redis:6379/5`
*   **`DSXCONNECT_WORKERS__BACKEND`**: `redis://redis:6379/6`
*   **`DSXCONNECT_REDIS_URL`**: `redis://redis:6379/3`
*   **`DSXCONNECT_DATABASE__TYPE`**: `sqlite3`
*   **`DSXCONNECT_DATABASE__LOC`**: `/app/data/dsx-connect.db.sql`
*   **`DSXCONNECT_DATABASE__RETAIN`**: `100`
*   **`DSXCONNECT_DATABASE__SCAN_STATS_DB`**: `/app/data/scan-stats.db.json`
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

If you enable the example DSXA subchart, dsx-connect automatically points to `http://<release>-dsxa-scanner:5000/scan/binary/v2` unless you override the scanner URL. If you previously set `DSXCONNECT_SCANNER__SCAN_BINARY_URL`, clear it (set empty) to use the auto value.

### DSXA Scanner URL and Overrides

By default, when `dsxa-scanner.enabled=true`, services compute the scanner URL from the Helm release name and sane defaults:

- Default service: `http://<release>-dsxa-scanner:5000/scan/binary/v2`
- Config knobs (optional) under `global.scanner`:
  - `global.scanner.serviceName`: override service DNS name
  - `global.scanner.port`: override port (default `5000`)
  - `global.scanner.scheme`: override scheme (`http`|`https`, default `http`)

You can also override the URL directly via env if you are pointing to an external DSXA:

- Set globally for all subcharts:
  - `--set-string global.env.DSXCONNECT_SCANNER__SCAN_BINARY_URL=http://external-dsxa:5000/scan/binary/v2`

- Or only for the API (example):
  - `--set-string dsx-connect-api.env.DSXCONNECT_SCANNER__SCAN_BINARY_URL=http://external-dsxa:5000/scan/binary/v2`

If you previously had a hardcoded API value that pointed to `dsxa-scanner` without the release prefix, remove it to fall back to the computed in-cluster service name.

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

The charts default image tag is a placeholder (e.g., `__VERSION__`). Override it at install/upgrade time — no need to edit `values.yaml`.

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
