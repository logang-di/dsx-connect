Central Dev TLS certs (for local HTTPS)

This shared folder is packaged into each connector image at /app/certs during invoke release. It is intended for development only.

- Generate self-signed certs:
  ./generate-dev-cert.sh

- Resulting files:
  - dev.localhost.crt
  - dev.localhost.key

- Enable in container via env:
  - DSXCONNECTOR_USE_TLS=true
  - DSXCONNECTOR_TLS_CERTFILE=/app/certs/dev.localhost.crt
  - DSXCONNECTOR_TLS_KEYFILE=/app/certs/dev.localhost.key

Replace with your own certs for staging/production or mount them at /app/certs.

