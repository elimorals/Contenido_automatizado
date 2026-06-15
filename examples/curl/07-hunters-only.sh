#!/usr/bin/env bash
# Devuelve los 12 candidates de los 4 hunters paralelos.
#
# Uso: ./07-hunters-only.sh "climate change"
set -euo pipefail

API_URL="${CONTENIDO_API_URL:-http://localhost:8000}"
TOPIC="${1:-climate change}"

echo "→ POST $API_URL/hunters"
RESP=$(curl -sf -X POST "$API_URL/hunters" \
    -H 'Content-Type: application/json' \
    -d @- <<EOF
{
    "topic": "$TOPIC",
    "mode": "premium"
}
EOF
)

echo "$RESP" | jq '.data.candidates | group_by(.angle) | map({angle: .[0].angle, count: length, claims: map(.core_claim)})'
