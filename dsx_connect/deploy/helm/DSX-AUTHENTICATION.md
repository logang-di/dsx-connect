# Connector ↔ dsx-connect Authentication (Enrollment + HMAC)

This document specifies the authentication model for securing traffic between connectors and the dsx-connect API using a single bootstrap enrollment secret and per‑connector HMAC. It replaces the legacy API key path; no backwards compatibility is kept for `DSXCONNECTOR_API_KEY`.

## Goals
- Toggle on/off per environment with a single switch.
- Simple bootstrap: a single enrollment token lets a connector register and receive per‑connector HMAC credentials.
- All subsequent dsx-connect ↔ connector calls use DSX‑HMAC (both directions).
- Safe rotation story for enrollment tokens and HMAC reprovisioning.
- Kubernetes‑friendly: secrets at install time and re‑use across upgrades.

## Modes and Toggles
- `DSXCONNECT_AUTH__ENABLED`: `true|false` (default: `false`).
    - When `false`, all auth checks are disabled.
    - When `true`, the system enforces DSX‑HMAC auth on connector‑only endpoints.

## Server (dsx-connect) Configuration
- `DSXCONNECT_AUTH__ENROLLMENT_TOKEN` or `DSXCONNECT_AUTH__ENROLLMENT_TOKENS` (CSV): one or more bootstrap tokens accepted by the API for initial registration.

Recommendation: source these from Kubernetes Secrets; see Helm notes below.

## Connector Configuration
- `DSXCONNECT_ENROLLMENT_TOKEN` (required when auth enabled): bootstrap secret used once at registration.
- The connector stores returned HMAC credentials in memory (not on disk) and uses them for DSX‑HMAC signing.

## Bootstrap & Runtime Flow
1) Startup: connector calls `POST /dsx-connect/api/v1/connectors/register` with header `X-Enrollment-Token: <bootstrap>` and the usual registration payload.
2) Server validates the enrollment token and completes registration.
3) Server provisions per‑connector HMAC credentials, stores them in Redis, and responds with:
   ```json
   { "connector_uuid": "...", "hmac_key_id": "...", "hmac_secret": "...", "status": "success" }
   ```
4) Subsequent calls use DSX‑HMAC in both directions:
   - dsx-connect → connector: HMAC on private endpoints.
   - connector → dsx-connect: HMAC on connector‑only endpoints.

## Protected Endpoints (when enabled)
- Enforce DSX‑HMAC on connector‑only endpoints you want private:
    - `POST /scan/request`
    - `POST /scan/jobs/{job_id}/enqueue_done`
    - `DELETE /connectors/unregister/{uuid}`
- Enforce DSX‑HMAC on connector private endpoints:
    - `POST /full_scan`, `POST /read_file`, `PUT /item_action`
    - `GET /repo_check`, `GET /estimate`, `GET /config`
- Optional to protect reads (`readyz`): recommended to keep open and restrict via NetworkPolicy/Ingress.

## HMAC Details
- Header: `Authorization: DSX-HMAC key_id=<kid>, ts=<unix>, nonce=<b64>, sig=<b64>`
- Canonical string: `METHOD|PATH?QUERY|ts|nonce|<body>`
- Server verifies: known `kid`, clock skew, signature equality (constant‑time), and nonce freshness (best‑effort)

## Server Implementation Sketch (FastAPI)
- Enrollment verification: `verify_enrollment_token(token: str) -> bool` (supports CSV via env).
- HMAC provisioning: generate per‑connector HMAC on register; store in Redis; return `hmac_key_id`/`hmac_secret`.
- Dependencies:
  - `require_dsx_hmac_inbound` for connector→dsx-connect protected endpoints.
  - Connector private router includes HMAC verification dependency for dsx-connect→connector calls.
- Endpoints:
  - `POST /connectors/register`: on success returns HMAC credentials (in addition to existing StatusResponse fields).

## Connector Implementation Sketch
- On startup: perform registration with `X-Enrollment-Token` and capture `hmac_key_id`/`hmac_secret`.
- Wrap the HTTP client: attach DSX‑HMAC automatically to dsx-connect calls.
- Verify DSX‑HMAC on private connector routes when `auth.enabled=true`.

## Helm & Secrets (Kubernetes)
- dsx‑connect chart:
    - Add optional secret templates for:
        - `auth-enrollment` (key: `ENROLLMENT_TOKEN`)
        - `auth-jwt` (key: `JWT_SECRET`)
    - Use Helm `lookup` to re‑use an existing enrollment token on upgrades if not provided explicitly.
    - Map to env:
        - `DSXCONNECT_AUTH__ENABLED=true`
        - `DSXCONNECT_AUTH__ENROLLMENT_TOKEN` from `auth-enrollment`
        - `DSXCONNECT_AUTH__JWT_SECRET` from `auth-jwt`
        - Optional: `DSXCONNECT_AUTH__ACCESS_TTL_SECONDS`, `__ISSUER`, `__AUDIENCE`, `__CLOCK_SKEW_SECONDS`.
- Connector charts:
    - Reference the same `auth-enrollment` Secret and set `DSXCONNECT_ENROLLMENT_TOKEN` accordingly.

### Secret Generation Options
- Provide both secrets explicitly via values (recommended for GitOps with external secret management).
- Or, on first install, generate a strong random `ENROLLMENT_TOKEN` and store it (Helm template + `lookup` reuse on upgrades). Do similar for `JWT_SECRET` or supply it.

## Local Dev / Docker Compose
- Toggle off by default.
- To try auth locally:
    - Set on dsx‑connect API: `DSXCONNECT_AUTH__ENABLED=true`, `DSXCONNECT_AUTH__ENROLLMENT_TOKEN`.
    - Set on the connector: `DSXCONNECT_ENROLLMENT_TOKEN` (match API).

## Rotation
- Enrollment token: support multiple tokens via CSV (`DSXCONNECT_AUTH__ENROLLMENT_TOKENS`) to allow overlap; update connector secrets and roll.
- HMAC reprovisioning: re‑register the connector (or add an admin endpoint) to mint a new key/secret and update Redis; connector will receive new creds.

## Error Semantics
- Missing/invalid HMAC: `401` with brief reason (no token challenges).
- Enrollment token invalid on register: `401`.

## Rollout Plan
1) Add auth enrollment to dsx‑connect and connector charts (disabled by default).
2) Implement per‑connector HMAC provisioning at registration and enforce DSX‑HMAC on both directions.
3) Enable in non‑prod: set enrollment secrets and `DSXCONNECT_AUTH__ENABLED=true`.
4) Monitor logs and tighten which endpoints enforce auth.
5) Enable in production.

## Security Notes
- Keep enrollment tokens limited in scope (connector→dsx‑connect only); treat as long‑lived secrets but rotate periodically.
- Prefer short access TTL (5–10 minutes) to limit exposure.
- Consider moving to asymmetric signing (RSA/ECDSA) if trust boundaries expand.

---

### Open Implementation Tasks (PRs welcome)
- Tests: integration test of register→HMAC→protected endpoints with/without auth enabled.

---

## Appendix: DSX‑HMAC curl Examples

These examples show how to sign connector→dsx‑connect requests using DSX‑HMAC from a trusted environment. Replace `<KID>`/`<SECRET>` with the per‑connector values returned by `/connectors/register`.

Helper (Python): emit Authorization header for a given request

```
python - << 'PY'
import base64, hashlib, hmac, os, sys, time, json

kid = os.environ['KID']          # export KID=<hmac_key_id>
sec = os.environ['SECRET']       # export SECRET=<hmac_secret>
method = sys.argv[1]
path_q = sys.argv[2]
body = sys.argv[3].encode() if len(sys.argv) > 3 else b''
ts = str(int(time.time()))
nonce = base64.b64encode(os.urandom(12)).decode()
msg = f"{method.upper()}|{path_q}|{ts}|{nonce}|".encode() + body
sig = base64.b64encode(hmac.new(sec.encode(), msg, hashlib.sha256).digest()).decode()
print(f"Authorization: DSX-HMAC key_id={kid}, ts={ts}, nonce={nonce}, sig={sig}")
PY
```

Example 1: `POST /dsx-connect/api/v1/scan/request`

```
export KID=...
export SECRET=...
HOST=https://dsx-connect.example.com
PATH=/dsx-connect/api/v1/scan/request
BODY='{"connector":{"uuid":"<uuid>"},"location":"s3://bucket/key","metainfo":"","connector_url":""}'
HDR=$(python - << 'PY'
import base64, hashlib, hmac, os, sys, time
kid=os.environ['KID']; sec=os.environ['SECRET']; method='POST'; path_q=os.environ['PATH']; body=os.environ['BODY'].encode(); ts=str(int(time.time())); nonce=base64.b64encode(os.urandom(12)).decode(); msg=f"{method}|{path_q}|{ts}|{nonce}|".encode()+body; sig=base64.b64encode(hmac.new(sec.encode(),msg,hashlib.sha256).digest()).decode(); print(f"Authorization: DSX-HMAC key_id={kid}, ts={ts}, nonce={nonce}, sig={sig}")
PY
)
curl -sS -X POST "$HOST$PATH" -H "$HDR" -H 'Content-Type: application/json' --data "$BODY"
```

Example 2: `POST /dsx-connect/api/v1/scan/jobs/{job_id}/enqueue_done`

```
export KID=...
export SECRET=...
JOB_ID=<job-id>
HOST=https://dsx-connect.example.com
PATH=/dsx-connect/api/v1/scan/jobs/$JOB_ID/enqueue_done
BODY='{"enqueued_total": 42}'
HDR=$(python - << 'PY'
import base64, hashlib, hmac, os, sys, time
kid=os.environ['KID']; sec=os.environ['SECRET']; method='POST'; path_q=os.environ['PATH']; body=os.environ['BODY'].encode(); ts=str(int(time.time())); nonce=base64.b64encode(os.urandom(12)).decode(); msg=f"{method}|{path_q}|{ts}|{nonce}|".encode()+body; sig=base64.b64encode(hmac.new(sec.encode(),msg,hashlib.sha256).digest()).decode(); print(f"Authorization: DSX-HMAC key_id={kid}, ts={ts}, nonce={nonce}, sig={sig}")
PY
)
curl -sS -X POST "$HOST$PATH" -H "$HDR" -H 'Content-Type: application/json' --data "$BODY"
```

Example 3: `DELETE /dsx-connect/api/v1/connectors/unregister/{uuid}`

```
export KID=...
export SECRET=...
UUID=<connector-uuid>
HOST=https://dsx-connect.example.com
PATH=/dsx-connect/api/v1/connectors/unregister/$UUID
HDR=$(python - << 'PY'
import base64, hashlib, hmac, os, sys, time
kid=os.environ['KID']; sec=os.environ['SECRET']; method='DELETE'; path_q=os.environ['PATH']; body=b''; ts=str(int(time.time())); nonce=base64.b64encode(os.urandom(12)).decode(); msg=f"{method}|{path_q}|{ts}|{nonce}|".encode()+body; sig=base64.b64encode(hmac.new(sec.encode(),msg,hashlib.sha256).digest()).decode(); print(f"Authorization: DSX-HMAC key_id={kid}, ts={ts}, nonce={nonce}, sig={sig}")
PY
)
curl -sS -X DELETE "$HOST$PATH" -H "$HDR"
```

Notes
- Always compute the header immediately before the call (ts/nonce changes each time).
- Ensure the `PATH` used in the signature exactly matches the HTTP request path and query.
- For large JSON bodies, keep `BODY` minified (no pretty‑printing) to match exact bytes.
