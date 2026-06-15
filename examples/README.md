# Examples

Ejemplos ejecutables listos para copiar.

## Estructura

```
examples/
├── curl/                  # Ejemplos curl puros (bash)
│   ├── 01-health.sh
│   ├── 02-generate-express.sh
│   ├── 03-generate-premium-topic.sh
│   ├── 04-generate-premium-article.sh
│   ├── 05-polling.sh
│   ├── 06-narrative-only.sh
│   ├── 07-hunters-only.sh
│   └── 08-list-tasks.sh
├── python/                # Cliente Python con httpx
│   ├── client.py          # Clase reutilizable ContenidoClient
│   ├── generate_express.py
│   ├── generate_premium.py
│   ├── narratives_ab_test.py
│   └── README.md
└── batch/                 # Scripts batch
    ├── topics.csv         # Sample input
    ├── batch_from_csv.sh
    └── batch_from_csv.py
```

## Quick start

### Curl
```bash
chmod +x examples/curl/*.sh
./examples/curl/01-health.sh
./examples/curl/02-generate-express.sh "Spring flowers"
```

### Python
```bash
uv run python examples/python/generate_express.py "Spring flowers"
```

### Batch (CSV)
```bash
./examples/batch/batch_from_csv.sh examples/batch/topics.csv
```

## Pre-requisitos

API corriendo en `http://localhost:8000`:
```bash
# Terminal 1
uv run uvicorn apps.api.main:app --port 8000
```

Variables (opcional, defaults a localhost):
```bash
export CONTENIDO_API_URL=http://localhost:8000
```
