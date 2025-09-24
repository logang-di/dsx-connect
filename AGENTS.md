# Repository Guidelines

## Project Structure & Modules
- `dsx_connect/`: Core FastAPI app, Celery workers, config, and release tasks.
- `connectors/<name>/`: Connector services (e.g., `aws_s3`, `filesystem`) with their own `CONNECTOR_VERSION` and deploy assets.
- `shared/`: Common utilities used by core and connectors.
- `scripts/`: Stack orchestration helpers (`stack-up.sh`, `stack-down.sh`, `stack-status.sh`).
- `tests/`: Pytest suite; collects `test_*.py` and skips vendor/`dist`.
- `dist/`: Bundled Docker Compose exports and release artifacts.

## Build, Test, Run
- Python 3.12. Create a venv and install: `python -m venv .venv && source .venv/bin/activate && pip install -r dsx_connect/requirements.txt`.
- Lint: `ruff check .` (target `py312` via `ruff.toml`).
- Test: `pytest -q` (configured by `pytest.ini`).
- Run API (dev): `uvicorn dsx_connect.app.dsx_connect_api:app --host 0.0.0.0 --port 8586`.
- Run workers (dev): `celery -A dsx_connect.taskworkers.celery_app worker -l INFO`.
- Bundles and releases (Invoke at repo root): `inv bundle`, `inv bundle-usetls`, `inv release-core`, `inv release-connectors --only=aws_s3,filesystem`, `inv release-all`, `inv helm-release`.
- Local stack from a bundle: `make up|down|status` (uses `scripts/stack-*.sh`; auto-detects latest `dist/dsx-connect-*`). Connector compose files follow `docker-compose-<connector>-connector.yaml` inside bundle subfolders.

## Coding Style & Naming
- 4-space indent, f-strings, add type hints where practical.
- Names: `snake_case` (functions/modules), `PascalCase` (classes), `UPPER_CASE` (constants).
- Versions: core `DSX_CONNECT_VERSION` in `dsx_connect/version.py`; connector `CONNECTOR_VERSION` in `connectors/<name>/version.py`.

## Testing Guidelines
- Framework: Pytest; files `test_*.py`. Exclusions for vendor, `dist`, venvs are set in `pytest.ini`.
- Add focused tests for new modules and bug fixes; keep tests fast.

## Commit & Pull Requests
- Commits: Imperative subject, optional scope (e.g., `core:`, `connector:aws_s3`).
- PRs: Clear description, linked issues, repro steps, and any screenshots/logs. Ensure `ruff` and `pytest` pass.

## Security & Configuration
- Do not commit secrets. Use env files (e.g., `dsx_connect/.dev.env`) or compose/Helm values.
- TLS: Prefer `inv bundle-usetls` for local HTTPS; status script supports `DSXCONNECT_USE_TLS` and CA bundles.
- Production TLS: terminate at the load balancer; use `inv bundle-usetls` only for local self-signed development.

## Next Steps
- Improve “full scan” UX: estimate counts up front, persist job progress, and support pause/cancel via Celery revoke + job state checks.

## Superlog (Scan-Result Logging) Roadmap
- Scope and separation: Use superlog only for operational scan-result events sent to SIEMs/receivers. Keep application/runtime logs on stdlib/console; do not route app logs through superlog.
- Event model: Define a stable `scan_result` event schema in superlog (job_id, scan_request_task_id, connector, location, verdict, threat, action, timestamps). Provide a helper `LogEvent.from_scan_result(...)` to build events from `ScanResultModel`.
- Destinations: Support pluggable outputs behind config: syslog (UDP/TCP/TLS), Splunk HEC, CloudWatch Logs, Azure Sentinel (DCR). Keep each destination small, with retries/backoff and bounded buffers. Start with syslog UDP and TLS.
- Formatters: Default to compact JSON for SIEMs. Keep RFC5424/syslog formatter available for environments that require it. Avoid mixing console formatting into superlog.
- Configuration: Add `DSXCONNECT_SUPERLOG__ENABLED` and `DSXCONNECT_SUPERLOG__DESTINATIONS` (CSV: syslog,splunk,cloudwatch,sentinel). Provide nested settings for each (host/port/facility/transport for syslog; url/token for Splunk; group/stream/region for CloudWatch; DCE/DCR/stream for Sentinel). Maintain compatibility with existing `DSXCONNECT_SYSLOG__*` when only syslog is enabled.
- Initialization: Build the scan-result log chain once per Celery worker process in `worker_process_init` using config. Expose a small accessor (e.g., `get_scan_result_chain()`); have the scan-result worker emit via this chain instead of direct syslog calls. Keep failures non-retriable for the task path.
- Backpressure and failure handling: For network failures, use short timeouts, exponential backoff, and bounded queues. Drop oldest or sample if buffers fill; always log a warning to console with task/job context.
- Security/PII: Provide field filtering/masking hooks (e.g., redact PII, truncate large paths). Ensure tokens/keys only come from env/secret stores and are never logged.
- Testing: Unit tests for formatters and acceptance filters; destination stubs with captured payloads; a UDP syslog test server for integration; config parsing tests per destination. Keep tests fast and hermetic.
- Migration: Keep `shared/log_chain.py` as a thin adapter temporarily, forwarding to superlog when enabled; deprecate once consumers switch. Document env var mapping and rollout steps.

## Current Status (UI + Workers)
- Event-driven progress: The UI no longer polls for job summaries. Progress and completion come from the scan-results SSE stream only.
- Buttons lifecycle: Cards flip to Pause/Cancel when a job starts, and revert to Full Scan on completion/cancel. A counts-based fallback flips immediately when processed >= total even before a final status frame.
- Final summary: On completion, the note shows “Scan complete: <processed> / <duration>”.
- Job id UX: Each running card shows a small job id pill next to the buttons with a copy-to-clipboard button. The pill survives button re-renders and hides on completion.
- Rehydrate on refresh: On load, the UI verifies any “active” jobs once via GET `/dsx-connect/api/v1/scan/jobs/{job_id}`. Completed/stale jobs are cleared so buttons return to Full Scan. No background polling is used.
- Version badge: The header shows `vX.Y.Z` (hidden in dev/unknown). `/meta` endpoint added; UI falls back to `/version` if `/meta` is unavailable.

### Completion and totals (2025-09-12)
- SSE payload now carries `enqueued_total`, `enqueued_count`, and `enqueue_done` in the `job` summary.
- Server sets `status=completed` in the SSE event when any reliable total is known and `processed_count >= total`.
- UI displays total as `total` (expected_total), else `enqueued_total`, else `enqueued_count`, and flips to Full Scan when processed reaches that value.
- Connectors POST `/scan/jobs/{job_id}/enqueue_done` with `enqueued_total` at the end of enqueue, enabling accurate completion without polling.

## Backend updates required
- Connectors: On full-scan completion of enqueue, connectors now POST `/dsx-connect/api/v1/scan/jobs/{job_id}/enqueue_done` with `{ enqueued_total }` to enable accurate completion.
- Workers: The scan-result worker marks a job `completed` when `processed_count >= (enqueued_total or expected_total)` and stamps `finished_at` (no polling assumptions). Restart Celery workers after pulling these changes.

### Observability / debugging
- Notify worker logs: `notify.scan_result job=<id> status=<s> processed=<n> total=<n|None> duration=<sec>`
- Completion logs: `job.complete job=<id> processed=<n> total=<n> finished_at=<ts>`
- Connector logs enqueue done: `enqueue_done posted job=<id> enqueued_total=<n> status=<code>`
- API can log SSE frames with `DSX_LOG_SSE_EVENTS=1`.
- Raw job state: `GET /dsx-connect/api/v1/scan/jobs/{job_id}/raw` returns the Redis hash and TTL.

## Manual steps to validate
- Restart services:
  - API: `uvicorn dsx_connect.app.dsx_connect_api:app --host 0.0.0.0 --port 8586`
  - Workers: `celery -A dsx_connect.taskworkers.celery_app worker -l INFO`
  - Connectors (to enable `enqueue_done` signaling)
- Hard refresh the UI.
- Trigger Full Scan and observe: Pause/Cancel appears, progress updates via events, and buttons revert to Full Scan with a final summary on completion.

## Known/observed
- If a reverse proxy strips `X-Job-Id`, the UI extracts `job_id` from the response description as a fallback.
- If a connector is down (`readyz` 502), the card may not refresh immediately. Once the connector is reachable and scan events flow, the card reconciles state.
- Pause/Resume: Endpoints are wired, but pause UX/state reconciliation remains WIP and will be revisited.
