# DSX‑Connect Helm Chart

This Helm chart deploys the DSX‑Connect stack (API + workers + Redis + optional Syslog), with an optional in‑cluster DSXA scanner for local testing.

This guide explains the core configuration concepts and details three deployment methods, from a quick local test to a production-grade GitOps workflow.

## Contents

- [Prerequisites](#prerequisites)
- [Core Configuration Concepts](#core-configuration-concepts)
- [Authentication Support](#authentication-support)
  - [Enrollment Token via Secret (optional, but recommended)](#enrollment-token-via-secret-optional-but-recommended)
  - [DIANNA API Token via Secret (required if DIANNA workers enabled)](#dianna-api-token-via-secret-required-if-dianna-workers-enabled)
- [Concurrency and Replicas](#concurrency-and-replicas)
- [Deployment Methods](#deployment-methods)
  - [Method 1: Quick Start (Command-Line Overrides)](#method-1-quick-start-command-line-overrides)
  - [Production Install (recommended flags)](#production-install-recommended-flags)
  - [Method 2: Standard Deployment (Custom Values File)](#method-2-standard-deployment-custom-values-file)
  - [Method 3: Production-Grade Deployment (GitOps & CI/CD)](#method-3-production-grade-deployment-gitops--cicd)
- [Scan Result Logging](#scan-result-logging)
- [Packaging & Publishing (Helm)](#packaging--publishing-helm)
- [Advanced Configuration: Overriding Default Environment Variables](#advanced-configuration-overriding-default-environment-variables)
- [Client Trust and CA Bundles](#client-trust-and-ca-bundles)
- [Verifying the Deployment](#verifying-the-deployment)
- [Full Configuration Parameters](#full-configuration-parameters)
- [Minimal configuration](#minimal-configuration)
- [Optional: Example DSXA Scanner](#optional-example-dsxa-scanner)
- [Image Version Overrides](#image-version-overrides)

## Prerequisites

- Kubernetes 1.19+ (a local cluster like Colima or Minikube is recommended for development).
- Helm 3.2+
- `kubectl` configured to point to your cluster.
- `openssl` for generating a self-signed certificate if you plan to enable TLS for development.

---

## Core Configuration Concepts

This umbrella chart deploys the DSX‑Connect stack. Key configuration areas (mirrors `values.yaml`):

1.  **global.env:** Minimal shared env (e.g., DSXA endpoint, Redis overrides).
    - DIANNA settings can also be set via the dedicated `global.dianna` block (see below).
2.  **global.image:** Optional image defaults inherited by subcharts.
3.  **global.scanner:** Hints for in‑cluster DSXA discovery (service name/port/scheme) when enabled.
4.  **dsx-connect-api:** API service and TLS settings; component‑specific env.
5.  **Worker charts:** Scan Request, Verdict Action, Results, Notification, DIANNA (env + Celery concurrency).
6.  **redis:** Message broker configuration (enabled by default).
7.  **rsyslog:** Optional rsyslog service for results.
8.  **dsxa-scanner:** Optional single‑pod DSXA for local testing (disabled by default).

 

<!-- Quick Reference removed; see Full Configuration Parameters below for canonical settings. -->

## Authentication Support

### Enrollment Token via Secret (optional, but recommended)

Use an enrollment token to bootstrap connector registration. After registration, HMAC authenticates both directions.

Option A (recommended): apply the provided Secret manifest and reference it implicitly

1) Copy and edit `dsx_connect/deploy/helm/auth-enrollment-secret.yaml` (set your namespace and token), then apply:

```bash
kubectl apply -f dsx_connect/deploy/helm/auth-enrollment-secret.yaml
```

2) Enable auth in values without embedding the token:

```yaml
dsx-connect-api:
  auth:
    enabled: true
    enrollment:
      key: ENROLLMENT_TOKEN
      # value: ""   # leave empty to use the external Secret created above
```

Option B (demo only): embed the token so the chart creates the Secret for you

```yaml
dsx-connect-api:
  auth:
    enabled: true
    enrollment:
      key: ENROLLMENT_TOKEN
      value: "<strong-random>"
```

Connector charts:
- Set `auth.enabled=true` to verify HMAC on private routes.
- Provide the same token to connectors via `dsxConnectEnrollment.secretName/key` to set `DSXCONNECT_ENROLLMENT_TOKEN`.
- Expose only `/webhook_event` via Ingress; use NetworkPolicies to allow traffic from dsx‑connect and your ingress controller.


Notes:
- Swagger remains available for docs; “Try it out” will not work for HMAC‑protected connector endpoints.
- Frontend (user) auth is separate (recommend an Ingress with OIDC/oauth2‑proxy in production).

### DIANNA API Token via Secret (required if DIANNA workers enabled)

For production, source the DIANNA API token from a Kubernetes Secret rather than embedding it in values or passing it on the CLI.

1) Create the Secret (example manifest provided):

   - Copy and edit `di-api-secret.yaml` in this directory (set your namespace and token), then apply:

   ```bash
   kubectl apply -f dsx_connect/deploy/helm/di-api-secret.yaml
   ```

   Or create directly via kubectl:

   ```bash
   kubectl -n <ns> create secret generic di-api \
     --from-literal=apiToken="$DI_TOKEN"
   ```

2) Configure values (preferred) and install:

   Set DIANNA values in a values file (avoid passing secrets on the CLI). Most defaults are reasonable; typically you only set `managementUrl` and enable the DI worker.

   ```yaml
   # values-dianna.yaml
   dsx-connect-dianna-worker:
     dianna:
       managementUrl: "https://di.example.com"
       # apiToken: "${DI_TOKEN_FROM_SECRET}"  # provided by Secret management workflow
     enabled: true
     celery:
       concurrency: 2
   ```

   Install:

   ```bash
   helm upgrade --install dsx dsx_connect/deploy/helm \
     -f dsx_connect/deploy/helm/values.yaml \
     -f values-dianna.yaml \
     --set-string global.env.DSXCONNECT_SCANNER__SCAN_BINARY_URL=https://my-dsxa.example.com/scan/binary/v2 \
     --set-string global.image.tag=<version>
   ```

3) Queue name and scaling:

   - Queue defaults to `dev.dsx_connect.analyze.dianna` (prefix derived automatically).
   - Override with `dsx-connect-dianna-worker.celery.queue` if you run multiple isolated environments against the same Redis broker.
  - Scale parallelism via `dsx-connect-dianna-worker.celery.concurrency`.

## Concurrency and Replicas

Workers scale with two knobs. Use them together for best results:

- Replica count (`replicaCount`): number of pods. Each pod has its own CPU/memory limits/requests and its own Celery process. Good for horizontal scaling and resilience.
- Concurrency (`celery.concurrency`): number of task workers inside one pod. Increases parallelism within a pod; shares that pod’s resources.

Guidance:
- The Scan request workers are generally the place to start with concurrency.  These workers take enqueued scan request tasks, reads a file from a connector, and sends the file to DSXA for scanning.
  Needless to say, a single pod / single celery worker can only handle a single scan request at a time.
- Start by raising `celery.concurrency` modestly (2–4), then add `replicaCount` to spread load across nodes.
- If CPU-bound within a pod, increase pod resources or add replicas. If I/O-bound (network/Redis/HTTP), modest concurrency increases often help.
- Example: 3 pods × concurrency 3 ≈ 9 workers on the queue.
- Scale downstream workers (verdict/result/notification) when increasing request throughput to avoid bottlenecks.

#### Practical Tuning Tips
- Continue favoring modest Celery concurrency (2–4) before adding pods; add replicas when you see CPU saturation or want resiliency.
- For connectors, bump `workers` to 2–4 if read_file handlers are CPU-bound or you want more in-pod parallel reads; add connector replicas if a single pod’s CPU or network is saturated, or for HA.
- If you notice uneven distribution across connector replicas due to HTTP keep-alive, higher Celery concurrency tends to open more connections and spread load better; you can also tune httpx connection limits if needed later.

#### Connector Replicas: What `replicaCount` Actually Does
- Setting a connector chart’s `replicaCount > 1` deploys multiple identical connector pods that each register independently with dsx-connect, each with a unique connector UUID. The UI will show multiple connectors for the same asset/filter.
- A Full Scan request (from the UI or API) targets a single registered connector instance. Increasing `replicaCount` does not parallelize a single full-scan enqueue path.
- Where replicas do help:
  - High availability (one pod can restart while another continues to serve), and
  - Serving concurrent `read_file` requests from the dsx-connect scan-request workers (Kubernetes Service balances connections across pods; higher Celery concurrency opens more connections and spreads load).
- To parallelize work across a single asset intentionally, prefer:
  - Increasing connector `workers` (Uvicorn processes) for in-pod concurrency, and/or
  - Running multiple connector releases with different `DSXCONNECTOR_FILTER` partitions (sharding), so Full Scan is performed in parallel across slices by distinct connector instances.

---

## Deployment Methods

This chart is flexible. The following methods show how to deploy it, from a simple test to a production-grade workflow.

Note on namespaces: the examples below assume the `default` namespace. If you deploy to a different namespace, add `-n <namespace>` to your `kubectl` and `helm upgrade --install` commands, and create Secrets in that namespace.

### Method 1: Quick Start (Command-Line Overrides)

This method is best for quick, temporary deployments, like for local testing. It uses the `--set` flag to provide configuration directly on the command line.

**1. Create the TLS Certificate Secret (if enabling TLS):**
   If you plan to enable TLS for the `dsx-connect-api` server, create a TLS Secret named after the component: `<release>-dsx-connect-api-tls`.
   ```bash
   # Example for release name "dsx" (namespace default)
   kubectl create secret tls dsx-dsx-connect-api-tls --cert=tls.crt --key=tls.key
   ```

**2. (Optional) Create the Enrollment Token Secret (if enabling Authentication):**
   Apply the provided auth enrollment secret manifest (edit namespace/token first). The chart expects a Secret named `<release>-dsx-connect-api-auth-enrollment`.
   ```bash
   # Example for release name "dsx": the Secret name must be dsx-dsx-connect-api-auth-enrollment
   kubectl apply -f dsx_connect/deploy/helm/auth-enrollment-secret.yaml
   ```

**3. (Optional) Create the DIANNA API Secret (if enabling DI workers):**
   Apply the provided DI secret manifest (edit token/namespace first):
   ```bash
   kubectl apply -f dsx_connect/deploy/helm/di-api-secret.yaml
   ```

**4. Deploy the Stack:**
   *   **Simplest deployment: deploys DSXA scanner and dsx-connect on the same cluster:**
        Development mode deployment with a local DSXA scanner.  Use the `values-dev.yaml` (or copy it) to set deploy dsx-connect with a dsxa-scanner.  
        You can change the values in `values-dev.yaml` to match your environment,
        but, overriding values in the command line allows for flexible deployment.  In this case the only 
        setting that needs to be set is global.image.tag.
       ```bash 
       helm upgrade --install dsx . -n <namespace> -f values-dev.yaml --set-string global.image.tag=0.2.82
       ```

   *   **Using an external DSX/A Scanner, HTTP deployment:**
        In this case, using the values.yaml (the default), DSXA scanner is not deployed, so the scan binary URL must be set. 
        You can either edit the values.yaml, or copy it and edit, or simply pass in settings on the helm arguments:
       ```bash
       helm upgrade --install dsx -n <namespace> -f values.yaml --set-string global.image.tag=0.2.82
         --set-string global.env.DSXCONNECT_SCANNER__SCAN_BINARY_URL=http://my-dsxa-url:5000/scan/binary/v2
       ```

   *   **For a TLS-enabled deployment:**
       ```bash
       helm upgrade --install dsx . -n <namespace> \
         --set-string
         --set dsx-connect-api.tls.enabled=true \
         --set dsx-connect-api.tls.secretName=my-dsx-connect-api-tls \
         --set-string global.env.DSXCONNECT_SCANNER__SCAN_BINARY_URL=https://my-dsxa.example.com/scan/binary/v2

   *   **For an Authentication-enabled (enrollment) deployment:**
       ```bash
       helm upgrade --install dsx . -n <namespace> \
         --set-string global.image.tag=0.2.82 \
         --set dsx-connect-api.auth.enabled=true
       ```

   *   **For a DIANNA-enabled deployment:**
       ```bash
       helm upgrade --install dsx . -n <namespace> \
         --set-string global.image.tag=0.2.82 \
         --set dsx-connect-dianna-worker.enabled=true \
         --set-string dsx-connect-dianna-worker.dianna.managementUrl=https://di.example.com
       ```

   *   **Combined TLS + Authentication + DIANNA (CLI):**
       ```bash
       # Pre-create the required secrets
       kubectl apply -f dsx_connect/deploy/helm/auth-enrollment-secret.yaml
       kubectl apply -f dsx_connect/deploy/helm/di-api-secret.yaml
       kubectl create secret tls my-dsx-connect-api-tls --cert=tls.crt --key=tls.key

       # Install with TLS + Auth + DIANNA enabled
       helm upgrade --install dsx . \
         --set-string global.image.tag=0.2.82 \
         --set-string global.env.DSXCONNECT_SCANNER__SCAN_BINARY_URL=https://external-dsxa.example.com/scan/binary/v2 \
         --set dsx-connect-api.tls.enabled=true \
         --set dsx-connect-api.tls.secretName=my-dsx-connect-api-tls \
         --set dsx-connect-api.auth.enabled=true \
         --set dsx-connect-dianna-worker.enabled=true \
         --set-string dsx-connect-dianna-worker.dianna.managementUrl=https://di.example.com
       ```
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
   - TLS (if enabling HTTPS for the API):
     ```bash
     kubectl create secret tls my-dsx-connect-api-tls --cert=tls.crt --key=tls.key
     ```
   - Enrollment token (if enabling Authentication):
     ```bash
     kubectl apply -f dsx_connect/deploy/helm/auth-enrollment-secret.yaml
     ```
   - DIANNA API token (if enabling DI workers):
     ```bash
     kubectl apply -f dsx_connect/deploy/helm/di-api-secret.yaml
     ```

**2. Create a Custom Values File:**
   Create a new file, for example `my-dsx-connect-values.yaml`, to hold your configuration.

   ```yaml
   # my-dsx-connect-values.yaml

   global:
     env:
       # REQUIRED when dsxa-scanner.enabled=false (default): point to your external DSXA
       DSXCONNECT_SCANNER__SCAN_BINARY_URL: "http://external-dsxa:5000/scan/binary/v2"
     dianna:
       managementUrl: "https://di.example.com"           # DSX management console base URL
       apiToken: "${DI_TOKEN_FROM_SECRET}"               # Prefer using a Secret + envFrom in production
       verifyTls: true
       caBundle: ""                                      # optional path if you mount a custom CA
       chunkSize: 4194304                                 # bytes
       timeout: 60                                        # seconds
       autoOnMalicious: false
     scanner:
       # serviceName: "dsx-connect-dsxa-scanner"  # defaults to "<release>-dsxa-scanner"
       # port: 5000
       # scheme: http

   dsx-connect-api:
     tls:
       enabled: true
       secretName: "my-dsx-connect-api-tls"
     auth:
       enabled: true
       enrollment:
         key: ENROLLMENT_TOKEN
         # value: ""  # leave empty when providing Secret via auth-enrollment-secret.yaml
     env:
       LOG_LEVEL: info

   dsx-connect-scan-request-worker:
     enabled: true
     env:
       LOG_LEVEL: info
     celery:
       concurrency: 2

   dsx-connect-dianna-worker:
     enabled: true
     env:
       LOG_LEVEL: info
     celery:
       # override only if you changed naming; default queue prefix is "dev"
       # queue: "custom-prefix.dsx_connect.analyze.dianna"
       concurrency: 2

  # Example: do not hardcode secrets here in production. Prefer pulling from a
  # Kubernetes Secret at install time as shown above.

   # ... configure other workers, redis, syslog as needed
   ```

**3. Install the Chart:**
   Install the chart, referencing your custom values file with the `-f` flag.
   ```bash
   helm install dsx-connect . -f my-dsx-connect-values.yaml
   ```

#### Example: Combined TLS + Authentication + DIANNA values (production)

```yaml
# values-prod.yaml
global:
  env:
    DSXCONNECT_SCANNER__SCAN_BINARY_URL: "https://external-dsxa.example.com/scan/binary/v2"
  dianna:
    managementUrl: "https://di.example.com"
    # apiToken is typically provided via a Secret (see di-api-secret.yaml)
    apiToken: "${DI_TOKEN_FROM_SECRET}"
    verifyTls: true
    chunkSize: 4194304
    timeout: 60
    autoOnMalicious: false

dsx-connect-api:
  tls:
    enabled: true
    secretName: "my-dsx-connect-api-tls"
  auth:
    enabled: true
    enrollment:
      key: ENROLLMENT_TOKEN
      # value: ""  # leave empty; provide Secret via auth-enrollment-secret.yaml
  env:
    LOG_LEVEL: info

dsx-connect-scan-request-worker:
  enabled: true
  env:
    LOG_LEVEL: info
  celery:
    concurrency: 2

dsx-connect-dianna-worker:
  enabled: true
  env:
    LOG_LEVEL: info
  celery:
    concurrency: 2
```

Install:

```bash
kubectl apply -f dsx_connect/deploy/helm/auth-enrollment-secret.yaml
kubectl apply -f dsx_connect/deploy/helm/di-api-secret.yaml
kubectl create secret tls my-dsx-connect-api-tls --cert=tls.crt --key=tls.key

helm upgrade --install dsx . -f values-prod.yaml \
  --set-string global.image.tag=<version>
```

### Method 3: Production-Grade Deployment (GitOps & CI/CD)

This is the definitive, scalable, and secure approach for managing production applications. It uses modern Continuous Delivery (CD) mechanisms.

**The Philosophy:**
Instead of running `helm` commands manually, you declare the desired state of your application in a Git repository. A GitOps tool (like **Argo CD** or **Flux**) runs in your cluster, monitors the repository, and automatically syncs the cluster state to match what is defined in Git.

**The Workflow:**
This involves storing environment-specific values files (e.g., `values-prod.yaml`) in a separate GitOps repository. The GitOps tool then uses these files to automate Helm deployments, providing a fully auditable and declarative system for managing your application lifecycle.

---
## Scan Result Logging

The `dsx-connect-results-worker` component is responsible for logging scan results to syslog.  By default, it sends syslog over TCP to port 514.  


This helm chart includes 
a rsyslog service that can be used to collect scan results within the cluster.  The rsyslog service is enabled 
by default, but can be disabled by setting `rsyslog.enabled=false` in the `values.yaml` file.  

If you need to change the default configuration or chose to send scan results to other collectors, see the file:
See the APPENDIX-LOG-COLLECTORS.md file for more information. 


---

## Packaging & Publishing (Helm)

You have a few good options to distribute this umbrella chart so others can install it with a single Helm command.

- Option A — OCI registry (recommended if you already push Docker images)
  - Package: `inv helm-package` (outputs `dist/charts/dsx-connect-<ver>.tgz`)
  - Login: `helm registry login registry-1.docker.io -u <user>`
  - Push: `inv helm-push-oci --repo=oci://registry-1.docker.io/dsxconnect`
  - Install: `helm install dsx-connect oci://registry-1.docker.io/dsxconnect/dsx-connect-chart --version <ver> -f values.yaml`
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

*   API keys removed (use JWT/HMAC flows instead)
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
    *   `DSXCONNECT_SCAN_RESULT_TASK_WORKER__SYSLOG_SERVER_URL`: `rsyslog`
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
    Refer to the client's (e.g., `connectors/azure_blob_storage/deploy/helm/DEVELOPER_README.md`) documentation for how to configure its `DSXCONNECTOR_CA_BUNDLE` and `DSXCONNECTOR_VERIFY_TLS` settings to trust this CA.

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

Commonly tuned values (by component):

- Global
  - `global.image.tag`: pin a specific image tag for all components.
  - `global.env.DSXCONNECT_SCANNER__SCAN_BINARY_URL`: REQUIRED when `dsxa-scanner.enabled=false` (external DSXA).
  - `dsx-connect-dianna-worker.dianna.*`: DIANNA settings for the DI worker; map directly to `DSXCONNECT_DIANNA__*` env.
- API
  - `dsx-connect-api.tls.enabled`: enable HTTPS for the API. Certs are loaded from Secret `<release>-dsx-connect-api-tls` and mounted at `/app/certs`; HTTPS listens on 443.
  - `dsx-connect-api.auth.enabled` + `dsx-connect-api.auth.enrollment.{key,value}`: enable HMAC auth and set enrollment token.
- Workers
  - `dsx-connect-*-worker.celery.concurrency`: per‑worker parallelism inside a pod.
  - `dsx-connect-*-worker.replicaCount`: number of pods (horizontal scale/HA).
- DIANNA
  - `dsx-connect-dianna-worker`: set `enabled`, `celery.concurrency`, and the `dianna.*` values for this worker.

```
## Minimal configuration

For most deployments you only need to set a few environment values once, at the top‑level `values.yaml` under `global.env`:

```
global:
  env:
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
