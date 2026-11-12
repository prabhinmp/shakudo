#!/usr/bin/env bash
# Mint a test JWT with plain bash + openssl (no Python needed).
# Usage:  SECRET=... ./scripts/mint-token.sh <viewer|operator|admin> '<namespaces-json>' [ttl-seconds]
# Example: SECRET=$SECRET ./scripts/mint-token.sh admin '["demo-*"]'
set -euo pipefail
: "${SECRET:?set SECRET to the API signing secret}"
ROLE="${1:?role: viewer|operator|admin}"; NS="${2:?namespaces as JSON, e.g. [\"demo-*\"]}"; TTL="${3:-3600}"
ISS="${JWT_ISSUER:-deploy-api}"; AUD="${JWT_AUDIENCE:-deploy-api}"; SUB="${JWT_SUB:-tester}"
b64url() { openssl base64 -A | tr '+/' '-_' | tr -d '='; }
H=$(printf '%s' '{"alg":"HS256","typ":"JWT"}' | b64url)
P=$(printf '{"sub":"%s","iss":"%s","aud":"%s","exp":%d,"role":"%s","namespaces":%s}' \
      "$SUB" "$ISS" "$AUD" "$(( $(date +%s) + TTL ))" "$ROLE" "$NS" | b64url)
S=$(printf '%s' "$H.$P" | openssl dgst -sha256 -hmac "$SECRET" -binary | b64url)
echo "$H.$P.$S"
