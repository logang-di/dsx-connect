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

## Env & secrets (never hardcode)
`APP_ENV` (dev|stag|prod); `REDIS_URL` (e.g., redis://localhost:6379/0); `DSXA_SCANNER_URL` (e.g., http://0.0.0.0:8080/scan/binary/v2); `DSX_API_KEY`; optional TLS toggles `USE_TLS=true|false`. If not set, ask for them; prefer `.env` and compose/Helm values.

## Tests & quality gates
- Unit: `pytest -q`.
- Lint/type: `ruff check . && ruff format --check . || true` and `mypy` if config exists.
- Minimal e2e smoke: start Redis + API, then post a small sample to the scan endpoint and assert 200.

## Build & ship
- Uses invoke to prepare scripts and build images (see `tasks.py`).
- If Dockerfile exists, use `docker build -t dsx-connect:latest .` and `docker push dsx-connect:latest`.
- If Helm charts exist under `charts/`, use `helm lint charts/dsx-connect && helm template ...` (prefer dry-runs).

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
