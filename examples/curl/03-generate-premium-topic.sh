#!/usr/bin/env bash
# Genera un reel modo premium desde un topic (DAG de 18 reasoners).
#
# Uso: ./03-generate-premium-topic.sh "the placebo effect"
set -euo pipefail

API_URL="${CONTENIDO_API_URL:-http://localhost:8000}"
TOPIC="${1:-the placebo effect}"
USE_VEO="${USE_VEO:-false}"

echo "→ POST $API_URL/videos (premium topic path)"
RESP=$(curl -sf -X POST "$API_URL/videos" \
    -H 'Content-Type: application/json' \
    -d @- <<EOF
{
    "topic": "$TOPIC",
    "mode": "premium",
    "aspect": "9:16",
    "visual_strategy": "hybrid",
    "use_veo": $USE_VEO,
    "subtitle_style": "word_burst"
}
EOF
)

echo "$RESP" | jq .
TASK_ID=$(echo "$RESP" | jq -r .data.task_id)

echo
echo "→ Polling task $TASK_ID..."
START=$(date +%s)

while true; do
    INFO=$(curl -sf "$API_URL/tasks/$TASK_ID")
    STATE=$(echo "$INFO" | jq -r .data.state)
    PROG=$(echo "$INFO" | jq -r .data.progress)
    NOW=$(date +%s)
    ELAPSED=$((NOW - START))

    case $STATE in
        1)
            echo "✓ Complete in ${ELAPSED}s"
            echo
            echo "--- Output ---"
            echo "$INFO" | jq '.data.videos'
            echo
            echo "--- Chosen essence ---"
            echo "$INFO" | jq '.data.essence'
            echo
            echo "--- Timings ---"
            echo "$INFO" | jq '.data.timings_s'
            echo
            echo "--- Cost breakdown ---"
            echo "$INFO" | jq '.data.cost_breakdown'
            exit 0
            ;;
        -1)
            echo "✗ Failed after ${ELAPSED}s"
            echo "$INFO" | jq '.data.timings_s'
            exit 1
            ;;
        *)
            printf "\r  [%3ds] state=%s progress=%s%%  " "$ELAPSED" "$STATE" "$PROG"
            sleep 2
            ;;
    esac
done
