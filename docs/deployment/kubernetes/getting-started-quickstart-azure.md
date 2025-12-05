# Quick Tutorial 2: Helm with local values (dsx-connect + DSXA + Azure Blob Connector)

This tutorial mirrors Tutorial #1 but targets the Azure Blob connector. Instead of inline `--set` overrides, we’ll pull the charts locally, edit `values.yaml`, and apply example Secret manifests. This approach scales better for GitOps or teams that prefer checked-in configs.

## Prerequisites

- Kubernetes 1.19+ cluster (Colima with `--kubernetes` works well for local runs).
- Helm 3.2+, kubectl, access to Docker Hub (`helm registry login registry-1.docker.io`).
- Azure storage connection string for a test container with sample files.
- DSXA appliance URL, token, and scanner ID.

## 1. Download & extract the Helm charts; set helper variables

```bash
# Example versions; substitute the chart versions you need.
mkdir -p charts && cd charts
helm pull oci://registry-1.docker.io/dsxconnect/dsx-connect-chart --version 0.3.46 --untar
helm pull oci://registry-1.docker.io/dsxconnect/azure-blob-storage-connector-chart --version 0.5.27 --untar
cd ..
```

```bash
export NAMESPACE=dsx-tutorial-2
export RELEASE=dsx-tutorial-2
export ENROLLMENT_TOKEN=$(uuidgen)
export DSXA_APPLIANCE_URL=your-dsxa-appliance.example.com
export DSXA_SCANNER_ID=1
export DSXA_TOKEN=changeme
```

```bash
kubectl create namespace $NAMESPACE
```

## 2. Create Secrets from example manifests

Open `dsx_connect/deploy/helm/examples/secrets/auth-enrollment-secret.yaml` and `connectors/azure_blob_storage/deploy/helm/azure-secret.yaml`. Edit the placeholders (or copy the files elsewhere, edit, and apply). Set:

- In `auth-enrollment-secret.yaml`: `metadata.name = ${RELEASE}-dsx-connect-api-auth-enrollment`, `metadata.namespace = ${NAMESPACE}`, and `stringData.token = ${ENROLLMENT_TOKEN}`.
- In `azure-secret.yaml`: `metadata.namespace = ${NAMESPACE}` and set `stringData.AZURE_STORAGE_CONNECTION_STRING`.

Apply both:

```bash
kubectl apply -f dsx_connect/deploy/helm/examples/secrets/auth-enrollment-secret.yaml
kubectl apply -f connectors/azure_blob_storage/deploy/helm/azure-secret.yaml
```

## 3. Pull charts locally

## 4. Edit values files

Create `values-dsx.yaml`:

```yaml
global:
  env:
    DSXCONNECT_SCANNER__SCAN_BINARY_URL: ""
dsxa-scanner:
  enabled: true
  env:
    APPLIANCE_URL: "${DSXA_APPLIANCE_URL}"
    TOKEN: "${DSXA_TOKEN}"
    SCANNER_ID: "${DSXA_SCANNER_ID}"
dsx-connect-api:
  auth:
    enabled: true
    enrollment:
      key: ENROLLMENT_TOKEN
```

Create `values-azure.yaml`:

```yaml
env:
  DSXCONNECTOR_ASSET: "mytestcontainer"
auth_dsxconnect:
  enabled: true
  enrollmentSecretName: azure-connector-env
  enrollmentKey: DSXCONNECT_ENROLLMENT_TOKEN
secrets:
  name: azure-connector-env
```

Replace the `${DSXA_*}` placeholders via an editor or by running `envsubst` (similar to the Secret step).

## 5. Install dsx-connect

```bash
helm upgrade --install $RELEASE ./charts/dsx-connect-chart \
  --namespace $NAMESPACE \
  -f values-dsx.yaml
```

Wait for pods:

```bash
kubectl get pods -n $NAMESPACE
```

## 6. Install the Azure Blob connector

```bash
helm upgrade --install azure-connector ./charts/azure-blob-storage-connector-chart \
  --namespace $NAMESPACE \
  -f values-azure.yaml
```

## 7. Verify and test

```bash
kubectl port-forward svc/${RELEASE}-dsx-connect-api 8586:8586 -n $NAMESPACE
```

Open `http://localhost:8586`, confirm the Azure connector card shows READY, and trigger a full scan. Upload a sample blob to the container to confirm dsx-connect picks it up.

## Cleanup

```bash
helm uninstall azure-connector -n $NAMESPACE
helm uninstall $RELEASE -n $NAMESPACE
kubectl delete namespace $NAMESPACE
rm -rf charts values-dsx.yaml values-azure.yaml dsx-auth-secret.yaml azure-connector-secret.yaml
```
