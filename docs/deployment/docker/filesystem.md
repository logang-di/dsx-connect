# Filesystem Connector — Docker Compose

This guide shows how to deploy the Filesystem connector with Docker Compose for quick testing/POV. The connector itself always reads from `/app/scan_folder` and writes quarantine actions to `/app/quarantine` inside the container, so your job is simply to mount whichever filesystem you want to scan (local folder, NAS, cloud share, etc.) to those paths.

## Prerequisites
- Docker installed locally (or a container VM)
- A host folder to scan (and optionally a quarantine folder), mounted into the container
- A Docker network shared with dsx‑connect (example: `dsx-connect-network`)

## Compose File
Use `connectors/filesystem/deploy/docker/docker-compose-filesystem-connector.yaml` as a starting point.

### Core connector env (common across connectors)

| Variable | Description |
| --- | --- |
| `DSXCONNECTOR_DSX_CONNECT_URL` | dsx-connect base URL (e.g., `http://dsx-connect-api:8586` on the shared Docker network). |
| `DSXCONNECTOR_ASSET` | Always `/app/scan_folder` inside the container; bind your host/NAS path to this mount. |
| `DSXCONNECTOR_FILTER` | Optional rsync-style rules evaluated relative to `/app/scan_folder`. |
| `DSXCONNECTOR_ITEM_ACTION` | What to do on malicious verdicts (`nothing`, `delete`, `move`, `move_tag`). Use `move`/`move_tag` to relocate files into the quarantine mount. |
| `DSXCONNECTOR_ITEM_ACTION_MOVE_METAINFO` | Target path interpreted by the connector when moving files (defaults to `/app/quarantine`). |

### Filesystem-specific settings

| Field / Env | Description |
| --- | --- |
| `SCAN_FOLDER_PATH` | Host or NAS path you want to scan (Compose anchor bound into `/app/scan_folder`). |
| `QUARANTINE_FOLDER_PATH` | Host or NAS path for quarantined files (bound into `/app/quarantine`). |
| `DSXCONNECTOR_ASSET_DISPLAY_NAME` | Overrides what the UI shows for the asset (set to the same host scan path for clarity). |
| `DSXCONNECTOR_MONITOR` | `true` to enable inotify-based monitoring of `/app/scan_folder`. |
| `DSXCONNECTOR_MONITOR_FORCE_POLLING` | `true` to poll instead of relying on inotify (useful for remote filesystems that don’t emit events). |

Example:
```bash
docker compose -f connectors/filesystem/deploy/docker/docker-compose-filesystem-connector.yaml up -d
```

## Configure Host Volumes (pre-flight)
The Compose file defines two anchors you **must** customize before running:

```yaml
x-common-paths: &common-paths
  SCAN_FOLDER_PATH: &scan-folder "/path/to/local/folder"
  QUARANTINE_FOLDER_PATH: &quarantine-folder "/path/to/local/folder/dsxconnect-quarantine"
```

- `SCAN_FOLDER_PATH` should point to the host directory you want to scan.
- `QUARANTINE_FOLDER_PATH` should point to the host directory where quarantined files can be moved.

Those anchors are referenced in the `volumes` section:

```yaml
    volumes:
      - type: bind
        source: *scan-folder        # host path to scan
        target: /app/scan_folder    # in-container path (matches DSXCONNECTOR_ASSET)
      - type: bind
        source: *quarantine-folder  # host path for quarantine moves
        target: /app/quarantine
```

Set the host paths once, then run `docker compose up`. No changes to `DSXCONNECTOR_ASSET` are required—the connector always operates on `/app/scan_folder`, which now maps to your chosen host directory. To keep the dsx-connect UI readable, also set `DSXCONNECTOR_ASSET_DISPLAY_NAME` (already wired in the Compose file) to the same host path so the card shows `/Users/.../SAMPLES/PDF` instead of `/app/scan_folder`.

### Local vs Remote Mounts
- **Local bind mounts** (default compose file): use `SCAN_FOLDER_PATH` / `QUARANTINE_FOLDER_PATH` anchors so Docker binds host directories into `/app/scan_folder` and `/app/quarantine`.
- **Remote/NAS mounts**: swap the scan volume with an NFS or SMB volume before binding to `/app/scan_folder`. The quarantine path can remain a local bind if desired, or also point to a NAS export.

Example NFS compose snippet (`docker-compose-filesystem-connector-nfs.yaml`):

```yaml
volumes:
  nfs_mount:
    driver: local
    driver_opts:
      type: "nfs"
      o: "addr=192.168.86.44,vers=3,nolock,tcp,resvport"
      device: ":/mnt/fileshare/scanshare"

services:
  filesystem_connector:
    volumes:
      - nfs_mount:/app/scan_folder
      - type: bind
        source: *quarantine-folder
        target: /app/quarantine
```

Update the `addr`, `device`, and NFS options to match your NAS. This mounts the remote export inside the container so `DSXCONNECTOR_ASSET=/app/scan_folder` still works unchanged.

Example SMB/CIFS snippet (requires `cifs-utils` on the Docker host):

```yaml
volumes:
  smb_mount:
    driver: local
    driver_opts:
      type: cifs
      o: "username=svcaccount,password=changeme,vers=3.0,uid=1000,gid=1000"
      device: "//fileserver01/share/scans"

services:
  filesystem_connector:
    volumes:
      - smb_mount:/app/scan_folder
      - type: bind
        source: *quarantine-folder
        target: /app/quarantine
```

Adjust the credentials, share path, and `uid/gid` to match your environment. CIFS behaves like any other bind once mounted inside the container.

Example AFS (OpenAFS) snippet (requires the host to have `openafs-client` and a mounted `/afs` tree):

```yaml
services:
  filesystem_connector:
    volumes:
      - type: bind
        source: /afs/yourcell.com/projects/scans
        target: /app/scan_folder
      - type: bind
        source: *quarantine-folder
        target: /app/quarantine
```

Make sure the Docker host’s AFS cache manager has tokens for the target path (`kinit` + `aklog`), and adjust the `/afs/...` path to match your cell. Once bound, the connector treats `/app/scan_folder` like any other volume.

## Assets and Filters
- `DSXCONNECTOR_ASSET` points to the path **inside the container** (default `/app/scan_folder`). Mount the host directory with `volumes:` so the container sees the real files, e.g., `./samples:/app/scan_folder`.
- For quarantine actions, mount a second host path (e.g., `./quarantine:/app/quarantine`) and set `DSXCONNECTOR_ITEM_ACTION_MOVE_METAINFO` if needed.
- Filters are always relative to the container path defined in `DSXCONNECTOR_ASSET`; they do **not** reference host paths directly.
- See Reference → [Assets & Filters](../../reference/assets-and-filters.md) for guidance on sharding and scoping.

## TLS Options
- `DSXCONNECTOR_USE_TLS`: Serve the connector over HTTPS (mount cert/key and enable as needed).
- `DSXCONNECTOR_TLS_CERTFILE` / `DSXCONNECTOR_TLS_KEYFILE`: Paths to the mounted certificate and private key when TLS is enabled.
- `DSXCONNECTOR_VERIFY_TLS`: Keep `true` (default) to verify dsx-connect’s certificate; set to `false` only for local dev.
- `DSXCONNECTOR_CA_BUNDLE`: Optional CA bundle path when verifying dsx-connect with a private CA.

## Notes
- Consider enabling monitor (`DSXCONNECTOR_MONITOR=true`) for real-time file change detection.
