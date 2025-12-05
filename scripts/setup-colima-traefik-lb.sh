#!/usr/bin/env bash
set -euo pipefail

# Configure Traefik as a LoadBalancer in Colima's k3s and apply the Colima LB ingress example.
#
# Usage:
#   scripts/setup-colima-traefik-lb.sh [--namespace <ns>] [--timeout <seconds>]
#
# Defaults:
#   namespace: default
#   timeout: 120
#
# Requires:
#   - kubectl configured to point to your Colima k3s cluster
#   - Colima started with --kubernetes (ideally with --network-address)

NS="default"
TIMEOUT=120

while [[ $# -gt 0 ]]; do
  case "$1" in
    --namespace|-n)
      NS="$2"; shift 2;;
    --timeout)
      TIMEOUT="$2"; shift 2;;
    -h|--help)
      sed -n '1,80p' "$0" | sed -n '1,40p'; exit 0;;
    *)
      echo "Unknown argument: $1" >&2; exit 2;;
  esac
done

if ! command -v kubectl >/dev/null 2>&1; then
  echo "kubectl not found in PATH" >&2
  exit 1
fi

echo "Patching Traefik service to type LoadBalancer..."
kubectl -n kube-system patch svc traefik -p '{"spec":{"type":"LoadBalancer"}}' >/dev/null

echo "Waiting for Traefik External IP (timeout: ${TIMEOUT}s)..."
EXT_IP=""
end=$((SECONDS + TIMEOUT))
while [[ $SECONDS -lt $end ]]; do
  # Try IP first, then hostname.
  EXT_IP=$(kubectl -n kube-system get svc traefik -o jsonpath='{.status.loadBalancer.ingress[0].ip}' 2>/dev/null || true)
  if [[ -z "$EXT_IP" ]]; then
    EXT_HOST=$(kubectl -n kube-system get svc traefik -o jsonpath='{.status.loadBalancer.ingress[0].hostname}' 2>/dev/null || true)
    if [[ -n "$EXT_HOST" ]]; then
      EXT_IP="$EXT_HOST"
    fi
  fi
  if [[ -n "$EXT_IP" ]]; then
    break
  fi
  sleep 2
done

if [[ -z "$EXT_IP" ]]; then
  echo "Failed to obtain External IP/hostname for Traefik within ${TIMEOUT}s." >&2
  echo "Ensure Colima is started with: colima start --kubernetes --network-address" >&2
  exit 1
fi

echo "Traefik External endpoint: $EXT_IP"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INGRESS_TMPL="${SCRIPT_DIR}/../dsx_connect/deploy/helm/examples/ingress/ingress-colima-lb.yaml"

if [[ ! -f "$INGRESS_TMPL" ]]; then
  echo "Template not found: $INGRESS_TMPL" >&2
  exit 1
fi

echo "Applying ingress with host: dsx-connect.${EXT_IP}.nip.io in namespace: ${NS}"
sed "s/CHANGE_ME_TRAEFIK_EXTERNAL_IP/${EXT_IP}/g" "$INGRESS_TMPL" \
  | kubectl -n "$NS" apply -f -

echo "Done. Try: http://dsx-connect.${EXT_IP}.nip.io"
