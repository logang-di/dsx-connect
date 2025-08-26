Dev TLS certs for dsx_connect API

This folder is packaged into the dsx_connect image at /app/certs during invoke release. It is intended for development only.

Generate self-signed certs:
  ./generate-dev-cert.sh

Then set envs in compose:
  DSXCONNECT_USE_TLS=true
  DSXCONNECT_TLS_CERTFILE=/app/certs/dev.localhost.crt
  DSXCONNECT_TLS_KEYFILE=/app/certs/dev.localhost.key

Replace with your own certs for staging/production or mount them at /app/certs.
