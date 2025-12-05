# AWS S3 Connector — Helm Deployment

Deploy the `aws-s3-connector-chart` (under `connectors/aws_s3/deploy/helm`) using the steps below, whether you work directly from the repo or from the OCI registry.

## Prerequisites

- Kubernetes 1.19+ cluster with `kubectl` access.
- Helm 3.2+.
- Access to `oci://registry-1.docker.io/dsxconnect/aws-s3-connector-chart`.

## Preflight Tasks

Create the AWS credentials Secret before installing:

```bash
kubectl create secret generic aws-credentials \
  --from-literal=AWS_ACCESS_KEY_ID=<key> \
  --from-literal=AWS_SECRET_ACCESS_KEY=<secret>
```

(`connectors/aws_s3/deploy/helm/aws-secret.yaml` contains a template if you prefer to edit/apply a manifest.)

## Configuration

### Required settings

- `env.DSXCONNECTOR_ASSET`: target bucket or `bucket/prefix`.
- `env.DSXCONNECTOR_FILTER`: optional rsync-style include/exclude set (see [Filter reference](../../reference/filters.md)).
- `env.DSXCONNECTOR_DISPLAY_NAME`: friendly label in the dsx-connect UI.
- `env.DSXCONNECTOR_ITEM_ACTION` plus `env.DSXCONNECTOR_ITEM_ACTION_MOVE_METAINFO`: remediation options.
- `workers`: Uvicorn workers per pod (default 1).
- `replicaCount`: number of pods (default 1).

Filters follow rsync semantics (`?`, `*`, `**`, `+`, `-`).

### dsx-connect endpoint

Defaults to `http://dsx-connect-api` (or `https://dsx-connect-api` when TLS enabled). Override via `env.DSXCONNECTOR_DSX_CONNECT_URL` if dsx-connect is exposed elsewhere.

### Authentication & TLS

--8<-- "deployment/includes/connector-auth-tls.md"

## Deployment

### Method 1 – OCI chart with CLI overrides (fastest)

```bash
helm install aws-invoices-dev oci://registry-1.docker.io/dsxconnect/aws-s3-connector-chart \
  --version <chart-version> \
  --set env.DSXCONNECTOR_ASSET=my-bucket \
  --set-string env.DSXCONNECTOR_FILTER="" \
  --set-string image.tag=<connector-version>
```

### Method 2 – Work from a pulled chart (edit values locally)

```bash
helm pull oci://registry-1.docker.io/dsxconnect/aws-s3-connector-chart --version <chart-version>
tar -xzf aws-s3-connector-chart-<chart-version>.tgz
cd aws-s3-connector-chart
```

Example values file:

```yaml
env:
  DSXCONNECTOR_ASSET: "invoices-bucket/prefix"
  DSXCONNECTOR_FILTER: "**/*.pdf"
image:
  tag: "<connector-version>"
```

Install from the extracted chart root (`.`):

```bash
helm install aws-invoices-dev . -f values-dev.yaml
```

### Method 3 – GitOps / production style

```bash
helm upgrade --install aws-prod oci://registry-1.docker.io/dsxconnect/aws-s3-connector-chart \
  --version <chart-version> \
  -f values-prod.yaml
```

## Verification

```bash
helm list
kubectl get pods
kubectl logs deploy/aws-s3-connector -f
```

## Scaling & tuning

- Raise `workers` for more concurrent `read_file` responses within a pod.
- Increase `replicaCount` for HA or to fan out item actions; each pod registers separately.
- Keep AWS throttling in mind when increasing concurrency; adjust filters to limit scope.

See `connectors/aws_s3/deploy/helm/values.yaml` for the exhaustive parameter reference.
