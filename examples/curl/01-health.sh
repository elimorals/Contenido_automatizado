#!/usr/bin/env bash
# Verifica que la API está corriendo.
set -euo pipefail

API_URL="${CONTENIDO_API_URL:-http://localhost:8000}"

echo "→ GET $API_URL/health"
curl -sf "$API_URL/health" | jq .

echo
echo "→ GET $API_URL/"
curl -sf "$API_URL/" | jq .
