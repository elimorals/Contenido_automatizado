#!/usr/bin/env bash
# Genera solo el ScriptDraft (sin video). Útil para A/B testing de narrativas.
#
# Uso: ./06-narrative-only.sh "the placebo effect"
set -euo pipefail

API_URL="${CONTENIDO_API_URL:-http://localhost:8000}"
TOPIC="${1:-the placebo effect}"

echo "→ POST $API_URL/narratives"
curl -sf -X POST "$API_URL/narratives" \
    -H 'Content-Type: application/json' \
    -d @- <<EOF | jq .
{
    "topic": "$TOPIC",
    "mode": "premium"
}
EOF
