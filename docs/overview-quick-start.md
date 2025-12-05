# Quick Start (Docker Compose)

Spin up DSX-Connect, DSXA, and a filesystem connector on a single machine using Docker Compose. The steps below assume you unpacked a release bundle (`dsx-connect-<version>.tar.gz`) that ships with the Compose files referenced here, and that Docker Desktop (or Docker Engine with the Compose plugin) is available locally.

## 1. Launch DSXA and DSX-Connect core

1. Unpack the release bundle and `cd` into the extracted directory.
2. (First run only) Create the shared bridge network used by every compose file:
   ```bash
   docker network create dsx-connect-network
   ```
3. Configure and start the Deep Instinct scanning appliance (DSXA) services. You can edit the compose file directly or use environment variables.

   - Option A (export variables):
     ```bash
     export APPLIANCE_URL="https://<di>.customers.deepinstinctweb.com"
     export TOKEN="<DSXA token>"
     export SCANNER_ID="<scanner id>"
     export IMAGE="dsxconnect/dpa-rocky9:4.1.1.2020"   # optional override
     export FLAVOR="rest,config"                       # optional override
     export NO_SSL="true"                              # optional override

     docker compose -f docker/docker-compose-dsxa.yaml up -d
     ```

   - Option B (env file you can hand off or re-use):
     ```bash
     cat > dsxa.env <<'EOF'
     APPLIANCE_URL=https://<di>.customers.deepinstinctweb.com
     TOKEN=<DSXA token>
     SCANNER_ID=<scanner id>
     IMAGE=dsxconnect/dpa-rocky9:4.1.1.2020   # optional override
     FLAVOR=rest,config                       # optional override
     NO_SSL=true                              # optional override
     EOF

     docker compose --env-file dsxa.env -f docker/docker-compose-dsxa.yaml up -d
     ```

   The compose file binds DSXA to the shared `dsx-connect-network` and exposes port `8080` on the host. Adjust the environment values above as needed; no YAML edits are required.
4. Start DSX-Connect core (API, workers, Redis, UI):
   ```bash
   docker compose -f docker/docker-compose-dsx-connect-all-services.yaml up -d
   ```
5. Confirm the stack is healthy:
   ```bash
   docker compose -f docker/docker-compose-dsx-connect-all-services.yaml ps
   ```
   You should see containers for the API (`dsx-connect-api`), workers, Redis, and supporting services. The UI becomes available at `http://localhost:8586/`.

## 2. Add a filesystem connector

1. Pick a host directory for testing:
   ```bash
   mkdir -p ~/Documents/dsx-connect-test
   ```
   Optionally drop a few sample files into `~/Documents/dsx-connect-test` so you have something to scan immediately.
2. Edit `filesystem-connector-<version>/docker/docker-compose-filesystem-connector.yaml` and update the two placeholders near the top:
   ```yaml
   x-common-paths:
     SCAN_FOLDER_PATH: &scan-folder "/Users/<you>/Documents/dsx-connect-test"
     QUARANTINE_FOLDER_PATH: &quarantine-folder "/Users/<you>/Documents/dsx-connect-test/dsxconnect-quarantine"
   ```
   These values are bind-mounted into the container so the connector can read and quarantine files from your host.
3. Set to Item Action to quarantine (move) malicious files: 
    ```yaml
    DSXCONNECTOR_ITEM_ACTION: "move" # defines what action, if any, for a connector to take on malicious files (nothing, delete, tag, move, move_tag)
    ```

3. Bring up the connector on the shared network:
   ```bash
   docker compose -f filesystem-connector-<version>/docker/docker-compose-filesystem-connector.yaml up -d
   ```
   Within a few seconds the connector registers with DSX-Connect and starts monitoring the folder (composition sets `DSXCONNECTOR_MONITOR=true` and enables polling by default).

## 3. Explore the UI and run a scan

1. Browse to `http://localhost:8586`.
2. On the **Connectors** tab you should see a card for `filesystem-connector` showing its status and metadata.

    - The gear icon reveals the effective runtime configuration (resolved asset path, monitoring settings, etc.).
    - **Preview** lists a handful of files straight from the connector. This is a great way to confirm the connector is pointed at the right folder.
    - **Sample Scan** triggers scans for the first five files in the monitored folder.

3. Try either flow:

    - Click **Full Scan** to enumerate every file under `~/Documents/dsx-connect-test` and send each one for inspection. Scan results appear in the **Scan Results** pane of the UI.
    - Drop a new file into the folder. The connector’s monitor fires a webhook event and the file shows up in **Scan Results** automatically. If you configured an item action (e.g., `move`), confirm that quarantined files land in `~/Documents/dsx-connect-test/dsxconnect-quarantine` once remediation runs.

Done!  You can click on scan results to see more details, or click **Full Scan** again to re-run the scan.


## 4. Tear down

```bash
docker compose -f docker/docker-compose-dsx-connect-all-services.yaml down
docker compose -f docker/docker-compose-dsxa.yaml down
docker compose -f filesystem-connector-<version>/docker/docker-compose-filesystem-connector.yaml down
```
