Shared development TLS certificates for local HTTPS testing.

Contents:
- dev.localhost.crt: self-signed certificate for localhost
- dev.localhost.key: matching private key

Usage examples:
- API: set `DSXCONNECT_USE_TLS=true`, point cert/key to `shared/deploy/certs/dev.localhost.{crt,key}`
- Connector (server): set `DSXCONNECTOR_USE_TLS=true` and point to the same cert/key
- Connector (client outbound): set `DSXCONNECTOR_VERIFY_TLS=true` and optionally `DSXCONNECTOR_CA_BUNDLE=shared/deploy/certs/dev.localhost.crt`

Note: Original copies also exist under `connectors/framework/deploy/certs`. We keep both to avoid breaking existing docs/paths.

