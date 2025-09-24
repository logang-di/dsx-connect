# Deploy with Docker Compose

This guide shows how to run the SharePoint connector with Docker Compose.

## Prerequisites
- Microsoft Graph credentials (tenant ID, client ID, client secret) with permissions to list/read (and optionally write/move/delete) files.
- A docker network shared with dsx-connect (example below uses `dsx-connect-network`).

## Example docker-compose service

Use the provided `deploy/docker/docker-compose-sharepoint-connector.yaml` as a starting point. Adjust:
- `DSXCONNECTOR_ASSET` to your site/library/folder URL.
- `DSXCONNECTOR_FILTER` for scoping; see filter rules below.
- Provide Graph credentials via environment variables.

Run:
```bash
docker compose -f deploy/docker/docker-compose-sharepoint-connector.yaml up -d
```

Notes:
- If dsx-connect runs with HTTPS and a private CA, mount the CA and set:
  - `DSXCONNECTOR_VERIFY_TLS=true`
  - `DSXCONNECTOR_CA_BUNDLE=/app/certs/ca.crt`

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
| "prefix/**"                                           | Everything under `prefix/` (common for “prefix” scoping)                    |
| "sub1"                                                | Files within subtree `sub1` (recurse into subtrees)                         |
| "sub1/*"                                              | Files directly under `sub1` (no recursion)                                  |
| "sub1/sub2"                                           | Files within subtree `sub1/sub2` (recurse)                                   |
| "*.zip,*.docx"                                        | All files with .zip and .docx extensions                                    |
| "-tmp --exclude cache"                                | Exclude `tmp` and `cache` directories                                       |
| "sub1 -tmp --exclude sub2"                            | Include `sub1` subtree but exclude `tmp` and `sub2`                         |
| "test/2025*/*"                                        | Files in subtrees matching `test/2025*/*` (no recursion)                    |
| "test/2025*/** -sub2"                                 | Recurse under `test/2025*/**`, excluding any `sub2` subtree                 |
| "'scan here' -'not here' --exclude 'not here either'" | Quoted tokens for names with spaces                                          |
