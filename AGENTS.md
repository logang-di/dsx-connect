# Connector ↔ dsx-connect Authentication (Enrollment + HMAC)

This document describes the current authentication model for securing traffic between connectors and the dsx-connect API using a single bootstrap enrollment secret and per‑connector HMAC. It replaces the legacy API key path and avoids long‑lived “connector tokens”.

## Goals
- Single on/off toggle per environment.
- Simple bootstrap: a single enrollment token lets a connector register and receive per‑connector HMAC credentials.
- All subsequent dsx-connect ↔ connector calls use DSX‑HMAC (both directions).
- Safe rotation story for enrollment tokens and HMAC reprovisioning.
- Kubernetes‑friendly: secrets at install time, re‑use across upgrades.

## Modes and Toggles
- `DSXCONNECT_AUTH__ENABLED`: `true|false` (default: `false`).
  - When `false`, all auth checks are disabled (useful for local/dev).
  - When `true`, the system enforces HMAC auth on connector‑only endpoints.

## Server (dsx-connect) Configuration
- `DSXCONNECT_AUTH__ENROLLMENT_TOKEN` or `DSXCONNECT_AUTH__ENROLLMENT_TOKENS` (CSV): one or more bootstrap secrets accepted by the API for initial registration.
- Optional JWT settings remain available but are not required for connector flows (HMAC is used after bootstrap).

Recommendation: source enrollment tokens from Kubernetes Secrets and rotate with CSV overlap.

## Connector Configuration
- `DSXCONNECT_ENROLLMENT_TOKEN` (required when auth enabled): bootstrap secret used once at registration.
- The connector stores returned HMAC credentials (key id + secret) in memory and uses them for DSX‑HMAC signing.
- No persistent connector tokens are used; no secrets are written to disk by the connector.

## Bootstrap & Runtime Flow
1) Startup: connector calls `POST /dsx-connect/api/v1/connectors/register` with header `X-Enrollment-Token: <bootstrap>` and the usual registration payload.
2) Server validates the enrollment token and completes registration.
3) Server provisions per‑connector HMAC credentials, stores them in Redis, and responds with:
   ```json
   { "connector_uuid": "...", "hmac_key_id": "...", "hmac_secret": "...", "status": "success", ... }
   ```
4) Both directions use DSX‑HMAC for all subsequent calls:
   - dsx-connect → connector (private): signed with the connector’s HMAC.
   - connector → dsx-connect (connector‑only endpoints): signed with the connector’s HMAC.
5) Webhook/Event: connector `POST /webhook_event` remains public but must validate provider signatures (e.g., Azure/GitHub) and should be exposed via Ingress only for that path.

## Protected Endpoints (when enabled)
- Connector private endpoints require DSX‑HMAC from dsx‑connect:
  - `POST /full_scan`
  - `POST /read_file`
  - `PUT /item_action`
  - `GET /repo_check`, `GET /estimate`, `GET /config`
- dsx-connect connector‑only endpoints require DSX‑HMAC from the connector (or enrollment token as an admin escape hatch):
  - `POST /scan/request`
  - `POST /scan/jobs/{job_id}/enqueue_done`
  - `DELETE /connectors/unregister/{uuid}`
- Health/read endpoints (e.g., `/healthz`, `/readyz`) may remain open; secure them via NetworkPolicy/Ingress if desired.

## HMAC Details
- Header: `Authorization: DSX-HMAC key_id=<kid>, ts=<unix>, nonce=<b64>, sig=<b64>`
- Canonical string: `METHOD|PATH?QUERY|ts|nonce|<body>` (where `<body>` is raw bytes).
- Server verifies: known `key_id`, clock skew, signature equality (constant‑time), and nonce freshness (best‑effort).
- Per‑connector secrets are generated on registration and stored in Redis under `config:<connector_uuid>`; a key‑id index maps `key_id` → `connector_uuid` for inbound lookups.

## Helm & Secrets (Kubernetes)
- dsx‑connect chart:
  - Set enrollment via Secret/env:
    - `DSXCONNECT_AUTH__ENROLLMENT_TOKEN` (single) or `DSXCONNECT_AUTH__ENROLLMENT_TOKENS` (CSV for rotation).
  - Auth toggle: `DSXCONNECT_AUTH__ENABLED=true` to enforce HMAC.
- Connector charts:
  - `dsxConnectEnrollment`: reference the same enrollment Secret and set `DSXCONNECT_ENROLLMENT_TOKEN`.
  - `auth.enabled`: controls HMAC verification on connector private endpoints.
  - Expose only `/webhook_event` via Ingress; add NetworkPolicies to allow ingress only from dsx-connect and your Ingress controller.

### Secret Generation Options
- Provide enrollment tokens explicitly via values (recommended for GitOps with external secret management).
- Or, on first install, generate a strong random `ENROLLMENT_TOKEN` and store it; rotate with CSV overlap when needed.

## Local Dev / Docker Compose
- Auth disabled by default (`DSXCONNECT_AUTH__ENABLED=false`).
- To try auth locally:
  - dsx‑connect API: set `DSXCONNECT_AUTH__ENABLED=true` and `DSXCONNECT_AUTH__ENROLLMENT_TOKEN`.
  - Connector: set `DSXCONNECT_ENROLLMENT_TOKEN` to match.

## Rotation
- Enrollment token: support multiple tokens via CSV (`DSXCONNECT_AUTH__ENROLLMENT_TOKENS`) to allow overlap; update connector secrets and roll.
- HMAC reprovisioning: re‑register the connector (or add an admin endpoint) to mint a new key/secret pair and update Redis; connector will receive new creds.

## Error Semantics
- Missing/invalid DSX‑HMAC: `401` (brief reason; no token challenges).
- Enrollment token invalid on register: `401`.

## Swagger & UI Notes
- Swagger remains available for docs. “Try it out” will not work for connector‑only protected endpoints because DSX‑HMAC secrets are not exposed to the browser (by design).
- Use curl/Postman with HMAC from a trusted environment to test protected endpoints, or disable auth in dev.
- Frontend (user) auth is separate from connector auth. In production, front dsx‑connect with an Ingress that enforces user authentication (e.g., OIDC via oauth2‑proxy). UI users never use enrollment or HMAC.

## Rollout Plan
1) Add auth enrollment to dsx‑connect and connector charts (disabled by default).
2) Implement per‑connector HMAC provisioning at registration and enforce DSX‑HMAC on both directions.
3) Enable in non‑prod: set enrollment secrets and `DSXCONNECT_AUTH__ENABLED=true`.
4) Monitor logs and tighten which endpoints enforce auth.
5) Enable in production.

## Security Notes
- Keep enrollment tokens limited in scope (connector→dsx‑connect only); rotate periodically with CSV overlap.
- DSX‑HMAC ties authentication to the exact request (method/path/body); secrets are per‑connector and can be reprovisioned.

## Current State Summary
- Removed: legacy API key path and any `DSXCONNECTOR_API_KEY` wiring in charts/compose.
- Removed: global outbound HMAC config; per‑connector HMAC is auto‑provisioned at registration.
- Removed: JWT requirement for connector flows; no connector tokens are used after bootstrap.
- Helm (dsx‑connect): `auth.enabled` and `auth.enrollment.{key,value}` only; charts create an enrollment Secret when `value` is set.
- Helm (connectors): `auth.enabled` (HMAC verify) and `dsxConnectEnrollment.secretName/key` for the bootstrap token; ingress/network policy templates limit public exposure to the webhook path.

## Recent Activity (Summary)
- Finalized auth model: single enrollment token + per‑connector HMAC used in both directions after registration (no connector Bearer/JWT).
- Server changes:
  - Provision per‑connector HMAC on register, store in Redis, return `hmac_key_id`/`hmac_secret` in response.
  - Added inbound DSX‑HMAC verifier for connector→dsx‑connect endpoints (e.g., `/scan/request`, `enqueue_done`, `unregister`).
- Connector changes:
  - On register, capture returned HMAC creds at runtime; sign all calls to dsx‑connect with DSX‑HMAC.
  - Enforce DSX‑HMAC on private connector routes when `auth.enabled=true`.
- Helm changes:
  - Simplified dsx‑connect auth values to just `auth.enabled` + `auth.enrollment.{key,value}`; removed outboundHmac and JWT settings.
  - Connector charts use `dsxConnectEnrollment` and `auth.enabled`; added Ingress (webhook‑only) and NetworkPolicy templates.
  - Moved DIANNA config from `global.dianna` to `dsx-connect-dianna-worker.dianna` (managementUrl/token per worker);
    updated DI worker template to read `.Values.dianna` and docs to favor values files + Secrets over CLI.
- Docs:
  - AGENTS.md and DSXAUTHENTICATION.md reflect Enrollment+HMAC; added Appendix with DSX‑HMAC curl examples.
  - dsx‑connect Helm README: added ToC, new Authentication section, enrollment via Secret, namespace notes, Method 1/2 combined TLS+Auth+DI examples; removed Quick Reference; consolidated into Full Configuration Parameters.
- Cleanup:
  - Removed legacy OAuth pieces, global outbound HMAC, and API key model/mentions.
