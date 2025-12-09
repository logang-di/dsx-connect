# Docker Compose Best Practices

Think of the Compose YAML as a template and `.env` files as the fill. Keep the YAML stable; swap `.env` files per environment (dev/stage/prod) to pin image tags and inject secrets without editing YAML.

## Core ideas
- Use `.env` files to pin images and supply secrets. Avoid editing `docker-compose-*.yaml`.
- Maintain one env file per environment: `.dev.env`, `.stage.env`, `.prod.env`.
- Pin all images in the env file (core, DSXA, connectors) so you know exactly what you’re running.
- Reuse the same env files when you move to Kubernetes (convert to a Secret).

## Sample `.env`
```bash
# Core + DSXA images (pin releases)
DSXCONNECT_IMAGE=dsxconnect/dsx-connect:1.2.3
DSXA_IMAGE=dsxconnect/dpa-rocky9:4.1.1.2020

# Connector image example
ONEDRIVE_IMAGE=dsxconnect/onedrive-connector:0.1.7

# Core auth (optional)
#DSXCONNECT_ENROLLMENT_TOKEN=abc123

# DSXA settings (if you run DSXA locally)
#APPLIANCE_URL=https://<di>.customers.deepinstinctweb.com
#TOKEN=<DSXA token>
#SCANNER_ID=<scanner id>

# OneDrive connector settings (example)
#ONEDRIVE_TENANT_ID=...
#ONEDRIVE_CLIENT_ID=...
#ONEDRIVE_CLIENT_SECRET=...
#ONEDRIVE_USER_ID=...
```
Compose reads `.env` automatically; use `--env-file` to point to a different one (e.g., `.stage.env`).

## How to run with env files
1. Copy the sample: `cp docker-compose.env.sample .dev.env` and edit values.
2. Run core (example):  
   ```bash
   docker network create dsx-connect-network || true
   docker compose --env-file .dev.env -f dsx_connect/deploy/docker/docker-compose-dsx-connect-all-services.yaml up -d
   ```
3. Run DSXA if needed:  
   ```bash
   docker compose --env-file .dev.env -f dsx_connect/deploy/docker/docker-compose-dsxa.yaml up -d
   ```
4. Run a connector (example OneDrive):  
   ```bash
   docker compose --env-file .dev.env -f connectors/onedrive/deploy/docker/docker-compose-onedrive-connector.yaml up -d
   ```

Swap `.dev.env` with `.stage.env` or `.prod.env` as needed; the YAML stays the same.

## Reuse for Kubernetes
Create a Secret from the same env file and reference it in Helm values:
```bash
kubectl create secret generic dsxconnect-env --from-env-file=.prod.env -n your-namespace
# In values.yaml (core + connectors):
# envSecretRefs:
#   - dsxconnect-env
```
This keeps configuration consistent across Compose and K8s.

## Tips
- Keep secrets out of YAML; store them in env files or your secret manager.
- Pin tags in env files; avoid `:latest` for anything shared.
- Use separate env files per environment; commit only samples (`*.env.sample`), not real secrets.
- For TLS, mount your CA bundle and set `DSXCONNECTOR_VERIFY_TLS=true` and `DSXCONNECTOR_CA_BUNDLE=...`.
