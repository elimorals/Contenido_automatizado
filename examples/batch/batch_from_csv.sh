#!/usr/bin/env bash
# Batch: lee topics de un CSV y los encola en la API.
#
# Uso: ./batch_from_csv.sh topics.csv [mode] [aspect]
#
# CSV format: una columna "topic" con header.
set -euo pipefail

API_URL="${CONTENIDO_API_URL:-http://localhost:8000}"
CSV_FILE="${1:?CSV file requerido}"
MODE="${2:-express}"
ASPECT="${3:-9:16}"

if [ ! -f "$CSV_FILE" ]; then
    echo "✗ CSV no existe: $CSV_FILE" >&2
    exit 1
fi

echo "→ Batch encolando desde $CSV_FILE (mode=$MODE, aspect=$ASPECT)"
echo

TASK_IDS=()
LINE_NUM=0

# Skip header
while IFS=, read -r topic _; do
    LINE_NUM=$((LINE_NUM + 1))
    [ "$LINE_NUM" -eq 1 ] && continue  # skip header
    [ -z "$topic" ] && continue          # skip empty

    # Trim whitespace y quotes
    topic=$(echo "$topic" | sed 's/^"//; s/"$//; s/^[[:space:]]*//; s/[[:space:]]*$//')

    echo "  [$LINE_NUM] $topic"

    RESP=$(curl -sf -X POST "$API_URL/videos" \
        -H 'Content-Type: application/json' \
        -d @- <<EOF
{
    "topic": "$topic",
    "mode": "$MODE",
    "aspect": "$ASPECT",
    "visual_strategy": "hybrid"
}
EOF
    ) || { echo "    ✗ Encolado falló"; continue; }

    TASK_ID=$(echo "$RESP" | jq -r .data.task_id)
    TASK_IDS+=("$TASK_ID")
    echo "    ✓ task_id: $TASK_ID"

done < "$CSV_FILE"

echo
echo "=== Encoladas ${#TASK_IDS[@]} tasks ==="
printf "Task IDs:\n"
printf "  %s\n" "${TASK_IDS[@]}"

echo
echo "Para hacer polling de todas:"
echo "  for tid in ${TASK_IDS[*]}; do ./examples/curl/05-polling.sh \$tid; done"
