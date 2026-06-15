#!/usr/bin/env bash
# Genera un reel modo express (MPT clásico) y hace polling hasta completar.
#
# Uso: ./02-generate-express.sh "Tu subject aquí"
set -euo pipefail

API_URL="${CONTENIDO_API_URL:-http://localhost:8000}"
SUBJECT="${1:-Spring flowers in Tokyo}"

echo "→ POST $API_URL/videos"
RESP=$(curl -sf -X POST "$API_URL/videos" \
    -H 'Content-Type: application/json' \
    -d @- <<EOF
{
    "subject": "$SUBJECT",
    "mode": "express",
    "aspect": "9:16",
    "voice_name": "en-US-AvaNeural-Female",
    "video_count": 1,
    "subtitle_style": "srt"
}
EOF
)

echo "$RESP" | jq .
TASK_ID=$(echo "$RESP" | jq -r .data.task_id)

echo
echo "→ Polling task $TASK_ID..."
while true; do
    INFO=$(curl -sf "$API_URL/tasks/$TASK_ID")
    STATE=$(echo "$INFO" | jq -r .data.state)
    PROG=$(echo "$INFO" | jq -r .data.progress)

    case $STATE in
        1)
            echo "✓ Complete (progress=$PROG)"
            echo "$INFO" | jq '.data.videos'
            echo "$INFO" | jq '.data.timings_s'
            exit 0
            ;;
        -1)
            echo "✗ Failed"
            echo "$INFO" | jq '.data.timings_s'
            exit 1
            ;;
        *)
            echo "  state=$STATE progress=$PROG%"
            sleep 3
            ;;
    esac
done
