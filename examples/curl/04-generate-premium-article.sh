#!/usr/bin/env bash
# Genera un reel premium desde una URL de artículo.
#
# Uso: ./04-generate-premium-article.sh "https://en.wikipedia.org/wiki/Placebo"
set -euo pipefail

API_URL="${CONTENIDO_API_URL:-http://localhost:8000}"
URL="${1:-https://en.wikipedia.org/wiki/Placebo}"

echo "→ POST $API_URL/videos (premium article path)"
RESP=$(curl -sf -X POST "$API_URL/videos" \
    -H 'Content-Type: application/json' \
    -d @- <<EOF
{
    "url": "$URL",
    "mode": "premium",
    "aspect": "9:16",
    "visual_strategy": "hybrid"
}
EOF
)

echo "$RESP" | jq .
TASK_ID=$(echo "$RESP" | jq -r .data.task_id)
echo
echo "Polling: ./examples/curl/05-polling.sh $TASK_ID"
