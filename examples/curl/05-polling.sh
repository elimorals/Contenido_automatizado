#!/usr/bin/env bash
# Hace polling a una task hasta que termine.
#
# Uso: ./05-polling.sh <task_id>
set -euo pipefail

API_URL="${CONTENIDO_API_URL:-http://localhost:8000}"
TASK_ID="${1:?task_id requerido}"

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
            echo "$INFO" | jq '.data'
            exit 0
            ;;
        -1)
            echo "✗ Failed after ${ELAPSED}s"
            echo "$INFO" | jq '.data'
            exit 1
            ;;
        *)
            printf "\r  [%3ds] state=%s progress=%s%%  " "$ELAPSED" "$STATE" "$PROG"
            sleep 2
            ;;
    esac
done
