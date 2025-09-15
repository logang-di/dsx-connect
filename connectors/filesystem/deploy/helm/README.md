# Filesystem Connector Helm Chart

This chart deploys the DSX-Connect Filesystem Connector to Kubernetes.
Use it to run a connector instance that scans a directory path available to the container and reports to `dsx-connect`.

## Prerequisites
- Kubernetes 1.19+
- Helm 3.2+
- `kubectl` configured for your cluster

---

## Quick Config Reference

- env.DSXCONNECTOR_ASSET: Base path inside the container to scan (default `/app/scan_folder`).
- env.DSXCONNECTOR_FILTER: Optional include/exclude rules under the asset root. Use to scope into subfolders (e.g., `subdir/**`). Follows rsync‑like rules. Examples: `"subdir/**"`, `"**/*.zip,**/*.docx"`, `"-tmp --exclude cache"`.
- env.DSXCONNECTOR_DISPLAY_NAME: Optional friendly name shown on the dsx-connect UI card (e.g., "TrueNAS Connector").
- env.DSXCONNECTOR_ITEM_ACTION: What to do with malicious files. One of: `nothing` (default), `delete`, `tag`, `move`, `move_tag`.
- env.DSXCONNECTOR_ITEM_ACTION_MOVE_METAINFO: Target directory when action is `move` or `move_tag` (default `/app/quarantine`). Ensure the volume and path exist.

These are the most commonly changed settings on first deploy.

FILTER (rsync‑like) quick cheat:
- `?` matches any single non-slash char; `*` matches 0+ non-slash; `**` matches 0+ including slashes.
- `-`/`--exclude` exclude rule; `+`/`--include` include rule; comma‑separate or space‑separate tokens.
 - See “Rsync‑Like Filter Rules” at the end of this document.

## Deployment Methods

This chart is flexible. The following methods show how to deploy it, from a simple test to a production-grade workflow.


### Method 1: Quick Start (Command-Line Overrides)
Install (configure mounts first; ASSET stays at `/app/scan_folder`):

- Release name must be unique. Suggested: `fs-<asset>-<env>` (e.g., `fs-scan-dev`).
- Do not change `env.DSXCONNECTOR_ASSET`; the chart automatically sets it to `scanVolume.mountPath` (default `/app/scan_folder`).
- Set up storage via `scanVolume.*` BEFORE install:
  - Existing PVC: create it first, then pass `--set scanVolume.enabled=true --set scanVolume.existingClaim=<pvc>`
  - Dev hostPath: pass `--set scanVolume.enabled=true --set scanVolume.hostPath=/path/on/node`
- Specify the image version when installing from this chart path.
  - From local path: add `--set-string image.tag=<version>`
  - From OCI (Method 3): use `--version <version>` instead

Examples

1) Using an existing PVC
```bash
helm install fs-scan-dev . \
  --set scanVolume.enabled=true \
  --set scanVolume.existingClaim=my-shared-pvc \
  --set-string env.DSXCONNECTOR_FILTER="" \
  --set-string image.tag=<version>
```

2) Using a hostPath (single-node dev only)
```bash
helm install fs-scan-dev . \
  --set scanVolume.enabled=true \
  --set scanVolume.hostPath=/mnt/data \
  --set-string env.DSXCONNECTOR_FILTER="" \
  --set-string image.tag=<version>
```

Enable TLS on the connector (optional):
```bash
kubectl create secret tls my-tls --cert=tls.crt --key=tls.key
helm upgrade --install fs-scan-dev . \
  --reuse-values \
  --set tls.enabled=true \
  --set tls.secretName=my-tls
```

### Method 2: Standard Deployment (values file)
Mounts first: set `scanVolume.*` (PVC or hostPath) so `/app/scan_folder` points at your data.

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
Mounts first: set `scanVolume.*` (PVC or hostPath) so `/app/scan_folder` points at your data.

```bash
helm install fs oci://registry-1.docker.io/dsxconnect/filesystem-connector \
  --version <ver> \
  --set env.DSXCONNECTOR_ASSET="/app/scan_folder" \
  --set-string env.DSXCONNECTOR_FILTER="**/*.zip,**/*.docx"
```

Note: OCI installs are prewired — the chart `--version` selects a chart whose `appVersion` becomes the default image tag. You can override with `--set-string image.tag=...`.

## Storage Mounts (scanVolume)
The filesystem connector must see the files to scan inside its pod. Configure a volume via `scanVolume`:

Example using an existing PVC:
```yaml
scanVolume:
  enabled: true
  existingClaim: my-shared-pvc
  mountPath: /app/scan_folder
```

Example hostPath for development (node-local, single-node clusters only):
```yaml
scanVolume:
  enabled: true
  hostPath: /mnt/data
  mountPath: /app/scan_folder
```

Notes
- The chart sets `DSXCONNECTOR_ASSET` to `scanVolume.mountPath` automatically (default `/app/scan_folder`).
- Prefer PVCs in production; hostPath is for local/dev.
- Helm guardrails: when `scanVolume.enabled=true`, the chart requires exactly one of `scanVolume.existingClaim` or `scanVolume.hostPath`. The install will fail if neither or both are set.

Example manifests are provided under `volume-mount-examples/`:
- `volume-mount-examples/pvc-scan-data.yaml` (simple RWO PVC)
- `volume-mount-examples/nfs-rwx.yaml` (static NFS PV/PVC for RWX)
- `volume-mount-examples/smb-rwx.yaml` (SMB PV/PVC using the SMB CSI driver)
- `volume-mount-examples/aws-efs-sc-pvc.yaml` (AWS EFS CSI dynamic provisioning; RWX)
- `volume-mount-examples/gcp-filestore-sc-pvc.yaml` (GKE Filestore CSI dynamic provisioning; RWX)

### Example PersistentVolumeClaim (PVC)
Create a PVC to back the scan directory (adjust storage class/size as needed):
```yaml
# pvc-scan-data.yaml
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: my-shared-pvc
spec:
  accessModes:
    - ReadWriteOnce   # use ReadWriteMany if your storage supports it and you need multi-pod access
  resources:
    requests:
      storage: 50Gi
  storageClassName: standard
```
Apply and install:
```bash
kubectl apply -f pvc-scan-data.yaml
helm install fs-prod . \
  --set scanVolume.enabled=true \
  --set scanVolume.existingClaim=my-shared-pvc \
  
```

### NFS (ReadWriteMany) Example
If you have an existing NFS server exporting a path, you can use a static PV/PVC to share the same content across multiple connector pods.

```yaml
# nfs-rwx.yaml
apiVersion: v1
kind: PersistentVolume
metadata:
  name: nfs-scan-pv
spec:
  # Important: disable default StorageClass to avoid dynamic provisioning and
  # ensure PV/PVC class matches for static binding.
  storageClassName: ""
  # Optional: mount options (version/proto) if required by your server
  # mountOptions:
  #   - nfsvers=4.1   # or nfsvers=3
  #   - proto=tcp
  capacity:
    storage: 500Gi
  accessModes:
    - ReadWriteMany
  persistentVolumeReclaimPolicy: Retain
  nfs:
    server: 10.0.0.12          # replace with your NFS server IP or DNS
    path: /exports/malware     # replace with your exported path
---
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: nfs-scan-pvc
spec:
  # Must match the PV storageClassName; empty string disables default class
  storageClassName: ""
  accessModes:
    - ReadWriteMany
  resources:
    requests:
      storage: 500Gi
  volumeName: nfs-scan-pv
```

Apply and install:
```bash
kubectl apply -f nfs-rwx.yaml
helm install fs-nfs . \
  --set scanVolume.enabled=true \
  --set scanVolume.existingClaim=nfs-scan-pvc 
```

If you see VolumeMismatch: storageClassName does not match, it usually means the PVC picked up the cluster's default StorageClass. Set `storageClassName: ""` on the PVC (and PV) and recreate the PVC so it binds to the static PV.

Node prerequisites (for NFS): each Kubernetes node that may host the pod must have the NFS client installed. Otherwise mounts fail with errors like "bad option; might need a /sbin/mount.nfs helper".

- Ubuntu/Debian nodes: `sudo apt-get update && sudo apt-get install -y nfs-common`
- RHEL/CentOS/Fedora nodes: `sudo yum install -y nfs-utils` or `sudo dnf install -y nfs-utils`
- Alpine-based nodes (e.g., some k3s or Colima VMs): `sudo apk add nfs-utils`

For Colima on macOS: `colima ssh` into the VM and install the NFS client there (e.g., `apk add nfs-utils`). For NFSv3, you may also need rpc.statd/rpcbind; prefer NFSv4 where possible or set `mountOptions` accordingly.

### Named StorageClass (Dynamic Provisioning)
If you have a StorageClass for RWX volumes (e.g., AWS EFS CSI, GKE Filestore CSI), use the provided examples to create a class and PVC. No static PV is needed; the provisioner will create it.

AWS EFS CSI (edit `fileSystemId` first):
```bash
kubectl apply -f volume-mount-examples/aws-efs-sc-pvc.yaml
helm upgrade --install fs-efs . \
  --set scanVolume.enabled=true \
  --set scanVolume.existingClaim=efs-scan-pvc
```

GKE Filestore CSI (ensure zone/region matches your cluster):
```bash
kubectl apply -f volume-mount-examples/gcp-filestore-sc-pvc.yaml
helm upgrade --install fs-filestore . \
  --set scanVolume.enabled=true \
  --set scanVolume.existingClaim=filestore-scan-pvc
```

Notes
- For AWS, prefer EFS (NFS) with the EFS CSI driver and a `ReadWriteMany` storage class.
- For GCP, consider Filestore (NFS) with the corresponding CSI or static PV.

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
| "subdir/**"                                           | Everything under `subdir/` (common folder scoping)                          |
| "sub1"                                                | Files within subtree `sub1` (recurse into subtrees)                         |
| "sub1/*"                                              | Files directly under `sub1` (no recursion)                                  |
| "sub1/sub2"                                           | Files within subtree `sub1/sub2` (recurse)                                   |
| "*.zip,*.docx"                                        | All files with .zip and .docx extensions                                    |
| "-tmp --exclude cache"                                | Exclude `tmp` and `cache` directories                                       |
| "sub1 -tmp --exclude sub2"                            | Include `sub1` subtree but exclude `tmp` and `sub2`                         |
| "'scan here' -'not here' --exclude 'not here either'" | Quoted tokens for names with spaces                                          |

### SMB (CIFS) Example
SMB requires the SMB CSI driver. Install it first: https://github.com/kubernetes-csi/csi-driver-smb

Create an SMB credentials Secret and a static PV/PVC:
```yaml
# smb-rwx.yaml
apiVersion: v1
kind: Secret
metadata:
  name: smb-credentials
type: Opaque
stringData:
  username: "YOUR_USER"
  password: "YOUR_PASS"
---
apiVersion: v1
kind: PersistentVolume
metadata:
  name: smb-scan-pv
spec:
  capacity:
    storage: 500Gi
  accessModes:
    - ReadWriteMany
  persistentVolumeReclaimPolicy: Retain
  csi:
    driver: smb.csi.k8s.io
    volumeHandle: smb-scan-vol-001              # unique ID for the PV
    volumeAttributes:
      source: "//smb-server.example.com/share"  # UNC path
    nodeStageSecretRef:
      name: smb-credentials
      namespace: default
---
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: smb-scan-pvc
spec:
  accessModes:
    - ReadWriteMany
  resources:
    requests:
      storage: 500Gi
  volumeName: smb-scan-pv
```

Apply and install:
```bash
kubectl apply -f smb-rwx.yaml
helm install fs-smb . \
  --set scanVolume.enabled=true \
  --set scanVolume.existingClaim=smb-scan-pvc 

### Method 4: Production-Grade Deployment (GitOps & CI/CD)

Manage environment-specific values in a GitOps repo and deploy this chart from an OCI registry with Argo CD or Flux.

## Troubleshooting Volume Mounts
- Permissions: ensure the mounted share allows the pod UID/GID to read/write. Adjust export/share permissions or set directory ownerships.
- Mount options: for SMB, consider `vers=3.0`, `dir_mode`, `file_mode`; for NFS, ensure appropriate export options (e.g., `no_root_squash` if needed). Configure via PV/StorageClass.
- SELinux/AppArmor: enforce correct labels on hostPath; consult your platform docs (OpenShift requires proper SELinux context).
- Verify mount in pod: `kubectl exec -it deploy/filesystem-connector -- sh -lc 'mount | grep /app/scan_folder && ls -lah /app/scan_folder'`.
```

SMB tips
- Ensure the cluster nodes can resolve and reach the SMB server and that firewall rules allow access (typically 445/TCP).
- On some environments, you may need to set mount options in the StorageClass/PV (e.g., `dir_mode`, `file_mode`, `vers`). See the SMB CSI driver docs.

## Connecting to dsx-connect
By default, the chart computes `DSXCONNECTOR_DSX_CONNECT_URL` as:
- `http://dsx-connect-api` when running without TLS
- `https://dsx-connect-api` when running with TLS
Override with `--set env.DSXCONNECTOR_DSX_CONNECT_URL=...` if `dsx-connect` is external.

## Verify
```bash
helm list
kubectl get pods
kubectl logs deploy/filesystem-connector -f
```

For all options, see `values.yaml`.

## TLS to dsx-connect (CA Bundle)

If dsx-connect uses a private/internal CA, set:
- `env.DSXCONNECTOR_VERIFY_TLS=true`
- `env.DSXCONNECTOR_CA_BUNDLE=/app/certs/ca/ca.crt`
- Mount the CA via a secret and add volume/volumeMount in your values.

## Image Version Overrides

- Local chart (this repo): the default image tag comes from the chart `appVersion` unless you override it (e.g., `--set-string image.tag=<version>`).
- OCI install (e.g., `helm install oci://… --version X.Y.Z`): the chart at that version is pulled and its `appVersion` becomes the default image tag. You can still override with `--set-string image.tag=...`.
