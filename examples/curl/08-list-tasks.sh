#!/usr/bin/env bash
# Lista las tasks recientes con paginación.
#
# Uso: ./08-list-tasks.sh [page] [page_size]
set -euo pipefail

API_URL="${CONTENIDO_API_URL:-http://localhost:8000}"
PAGE="${1:-1}"
PAGE_SIZE="${2:-10}"

echo "→ GET $API_URL/tasks?page=$PAGE&page_size=$PAGE_SIZE"
curl -sf "$API_URL/tasks?page=$PAGE&page_size=$PAGE_SIZE" | jq \
    '.data | {total, page, page_size, tasks: [.tasks[] | {task_id, state, mode, progress, total_s: .timings_s.total}]}'
