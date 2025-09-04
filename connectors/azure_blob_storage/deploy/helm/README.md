# Azure Blob Storage Connector Helm Chart

This Helm chart provides a flexible and secure way to deploy the DSX-Connect Azure Blob Storage Connector to a Kubernetes cluster.

This guide explains the core configuration concepts and details three deployment methods, from a quick local test to a production-grade GitOps workflow.

## Prerequisites

- Kubernetes 1.19+ (a local cluster like Colima or Minikube is recommended for development).
- Helm 3.2+
- `kubectl` configured to point to your cluster.
- `openssl` for generating a self-signed certificate if you plan to enable TLS for development.

---

## Deployment Methods

This chart is flexible. The following methods show how to deploy it, from a simple test to a production-grade workflow.

### Method 1: Quick Start (Command-Line Overrides)

This method is best for quick, temporary deployments, like for local testing. It uses the `--set` flag to provide configuration directly on the command line.

**1. Create the Azure Secret:**
   First, apply the `azure-secret.yaml` manifest after filling in your connection string.
   ```bash
   kubectl apply -f azure-secret.yaml
   ```

**2. Deploy the Connector:**

   *   **For a simple HTTP deployment:**
       ```bash
       # Replace 'my-container' with the name of your Azure blob container
       helm install http-connector . --set env.DSXCONNECTOR_ASSET=my-container
       ```

   *   **For a TLS-enabled deployment:**
       First, create your TLS secret (`kubectl create secret tls my-tls --cert=... --key=...`). Then, add the TLS flags to the install command:
       ```bash
       helm install tls-connector . \
         --set env.DSXCONNECTOR_ASSET=my-container \
         --set tls.enabled=true \
         --set tls.secretName=my-tls
       ```

### Method 2: Standard Deployment (Custom Values File)

This is the most common and recommended method for managing deployments. It involves creating a dedicated values file for each instance of the connector.

**1. Create the Required Secrets:**
   Apply both your Azure secret and, if enabling TLS, your TLS secret.
   ```bash
   kubectl apply -f azure-secret.yaml
   kubectl create secret tls my-tls --cert=tls.crt --key=tls.key
   ```

**2. Create a Custom Values File:**
   Create a new file, for example `my-connector-values.yaml`, to hold your configuration.

   ```yaml
   # my-connector-values.yaml

   # Set the target asset for this connector instance
   env:
     DSXCONNECTOR_ASSET: "my-production-container"

   # Enable TLS and specify the secret to use
   tls:
     enabled: true
     secretName: "my-tls"
   ```

**3. Install the Chart:**
   Install the chart, referencing your custom values file with the `-f` flag.
   ```bash
   helm install my-connector . -f my-connector-values.yaml
   ```

### Method 3: Production-Grade Deployment (GitOps & CI/CD)

This is the definitive, scalable, and secure approach for managing production applications. It uses modern Continuous Delivery (CD) mechanisms.

**The Philosophy:**
Instead of running `helm` commands manually, you declare the desired state of your application in a Git repository. A GitOps tool (like **Argo CD** or **Flux**) runs in your cluster, monitors the repository, and automatically syncs the cluster state to match what is defined in Git.

**The Workflow:**
This involves storing environment-specific values files (e.g., `values-invoices-prod.yaml`) in a separate GitOps repository. The GitOps tool then uses these files to automate Helm deployments, providing a fully auditable and declarative system for managing your application lifecycle.

---

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

---

## Advanced Deployment: Multi-Cluster Configuration

By default, this chart is configured to connect to a `dsx-connect-api` service running in the same Kubernetes cluster. It dynamically constructs the URL based on the service name (`dsx-connect-api`) and whether TLS is enabled for the connector.

In advanced scenarios, you may need to connect to a `dsx-connect` instance running in a different cluster or at an external URL. To do this, you can override the `DSXCONNECT_DSX_CONNECT_URL` environment variable.

**Example `my-external-connector-values.yaml`:**
```yaml
env:
  DSXCONNECTOR_ASSET: "my-container"
  # Override the default URL to point to an external service
  DSXCONNECT_DSX_CONNECT_URL: "https://my-dsx-connect.example.com:443"

# You will likely also need to provide a CA bundle to trust the external endpoint
# (See documentation on CA bundles)
tls:
  enabled: true
  secretName: "my-tls-secret"
```

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

```
