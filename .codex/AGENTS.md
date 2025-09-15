# Codex Project Guide — DSX-connect

## What this repo is
Python 3.12 FastAPI + Celery workers + Redis + Connectors. Services: `dsx-connect-api`, Celery task workers (`scan`, `verdict`, `results`), DSXA scanner (external), and connector microservices (`aws_s3`, `azure_blob_storage`, `google_cloud_storage`, `filesystem`, `sharepoint`).

## How to run (local, dev)
- Python env: `.venv/bin/activate && pip install -U pip && pip install -e ".[dev]"`.
- Compose (if present): `docker compose -f docker-compose-dsx-connect-all-services.yaml up -d`.
- Compose (if present): `docker compose -f docker-compose-<<connector name>>-connector.yaml up -d`.
- Or run services directly:
    - API: `uvicorn dsx_connect.app:app --reload --port 8000`.
    - Workers: `celery -A dsx_connect.taskworkers.app worker -Q scan,verdict,results -l INFO`.
- Quick health: `curl http://localhost:8000/dsx-connect/api/v1/test/dsxa-connection` and `redis-cli ping`.

## Stack scripts (bundle orchestration)

After running a full release that generates a bundled export under `dist/dsx-connect-<version>`, you can bring up/down the entire stack (DSXA, dsx-connect API + workers, and all connectors) and check status using helper scripts:

- Bring up everything:
  - `scripts/stack-up.sh` (auto-detects latest `dist/dsx-connect-*`)
  - or `scripts/stack-up.sh dist/dsx-connect-0.1.48`
  - filter connectors at runtime:
    - `scripts/stack-up.sh dist/dsx-connect-0.1.48 --only=filesystem,aws-s3`
    - `scripts/stack-up.sh dist/dsx-connect-0.1.48 --skip=sharepoint`
    - or with env vars: `CONNECTORS_ONLY=filesystem,aws-s3 scripts/stack-up.sh <bundle>`

- Bring down everything:
  - `scripts/stack-down.sh`
  - or `scripts/stack-down.sh dist/dsx-connect-0.1.48`
  - supports the same `--only/--skip` filters and `CONNECTORS_ONLY/CONNECTORS_SKIP` env vars

- Check status (TLS-aware):
  - `scripts/stack-status.sh`

Make targets (shortcuts)
- `make up`           → runs `scripts/stack-up.sh` (optional `BUNDLE=dist/dsx-connect-<ver>`) 
-    - filters: `CONNECTORS_ONLY=a,b make up BUNDLE=...` or `make up BUNDLE=... --only=a,b`
- `make down`         → runs `scripts/stack-down.sh` (optional `BUNDLE=...`)
-    - filters: `CONNECTORS_SKIP=sharepoint make down BUNDLE=...` or `make down BUNDLE=... --skip=sharepoint`
- `make status`       → runs `scripts/stack-status.sh`

Environment toggles for TLS used by status (and compatible with curl)
- `DSXCONNECT_USE_TLS`: set `true` to probe dsx-connect via `https` (default `false`).
- `DSXCONNECT_CA_BUNDLE`: path to a CA cert to verify dsx-connect (e.g., `shared/deploy/certs/dev.localhost.crt`). If not set and TLS is on, the script defaults to `-k` unless `CURL_INSECURE=false`.
- `CONNECTOR_USE_TLS`: set `true` to probe connectors via `https` (defaults to `DSXCONNECT_USE_TLS`).
- `CONNECTOR_CA_BUNDLE`: path to a CA cert to verify connectors.
- `CURL_INSECURE`: when `true` (default under TLS), uses `-k` to allow self-signed; set `false` to require a CA bundle.
- Optional host/port overrides: `API_HOST` (default `localhost`), `API_PORT` (default `8586`), `CONN_HOST` (default `localhost`).

Examples
- Probe everything with self-signed certs, skipping verification:
  - `DSXCONNECT_USE_TLS=true CONNECTOR_USE_TLS=true scripts/stack-status.sh`
- Probe with verification:
  - `CURL_INSECURE=false DSXCONNECT_USE_TLS=true DSXCONNECT_CA_BUNDLE=shared/deploy/certs/dev.localhost.crt \
     CONNECTOR_USE_TLS=true CONNECTOR_CA_BUNDLE=shared/deploy/certs/dev.localhost.crt \
     scripts/stack-status.sh`

## Env & secrets (never hardcode)
`APP_ENV` (dev|stag|prod); `REDIS_URL` (e.g., redis://localhost:6379/0); `DSXA_SCANNER_URL` (e.g., http://0.0.0.0:8080/scan/binary/v2); optional TLS toggles `USE_TLS=true|false`. If not set, ask for them; prefer `.env` and compose/Helm values.

## Tests & quality gates
- Unit: `pytest -q`.
- Lint/type: `ruff check . && ruff format --check . || true` and `mypy` if config exists.
- Minimal e2e smoke: start Redis + API, then post a small sample to the scan endpoint and assert 200.

## Build & ship
- Uses invoke to prepare scripts and build images (see `tasks.py`).
- If Dockerfile exists, use `docker build -t dsx-connect:latest .` and `docker push dsx-connect:latest`.
- If Helm charts exist under `charts/`, use `helm lint charts/dsx-connect && helm template ...` (prefer dry-runs).

TLS bundles
- Plain HTTP bundle: `inv bundle`
- TLS-enabled bundle: `inv bundle-tls` (alias) or `inv bundle-usetls`
  - Switches connector/API URLs to https and enables TLS env vars in compose files

## Guardrails for Codex (important)
- Don’t rename Celery queues, pub/sub channels, or API routes without updating all producers/consumers.
- DB migrations: use **migrate-forward** and keep responses backward-compatible; never drop/rename fields in one step.
- For risky changes, add a feature flag; keep deploy ≠ release (flags default OFF).
- Always show diffs and ask before writing; run tests before proposing commits.

## Project style
- Python: prefer Ruff + Black defaults; docstrings for public functions; Conventional Commits (`feat:`, `fix:`, etc.).
- PR checklist Codex should follow: tests added/updated, docs touched if behavior changed, changelog entry created.

## High-leverage tasks Codex can do here
- “Write unit tests for `shared.file_ops` and fix flaky behavior.”
- “Add SSE broadcast to `/subscribe/connector-registered` with reconnection logic.”
- “Create Helm values for enabling TLS (ingress + service) behind a toggle and validate with `helm lint`.”

## Progress/Next steps
- Complete Helm charts for all connectors and add a template for cookiecutter

