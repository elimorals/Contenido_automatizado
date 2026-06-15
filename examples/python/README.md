# Ejemplos Python

Clientes Python listos para copiar y usar.

## Pre-requisitos

```bash
# Si no estás en venv del proyecto
pip install httpx
```

## Scripts disponibles

### `client.py`
Clase reutilizable `ContenidoClient` con todos los endpoints. Importa desde tus scripts:

```python
from client import ContenidoClient

with ContenidoClient("http://localhost:8000") as client:
    task_id = client.create_video(topic="placebo", mode="premium")
    result = client.wait_for_task(task_id)
    print(result.videos)
```

### `generate_express.py`
Genera un reel modo express con progress callback.
```bash
python generate_express.py "Spring flowers"
```

### `generate_premium.py`
Genera un reel premium (DAG profundo).
```bash
python generate_premium.py "the placebo effect"
USE_VEO=true python generate_premium.py "fingerprints"
```

### `narratives_ab_test.py`
Genera 3 narrativas del mismo topic y compara hooks/payoffs.
```bash
python narratives_ab_test.py "climate change"
```

## Patrones avanzados

### Async client (usando httpx.AsyncClient)

Si necesitas concurrency, adapta `client.py` así:

```python
import asyncio
import httpx

async def generate_many(topics: list[str]):
    async with httpx.AsyncClient(base_url="http://localhost:8000") as client:
        tasks = [
            client.post("/videos", json={"topic": t, "mode": "express"})
            for t in topics
        ]
        responses = await asyncio.gather(*tasks)
        return [r.json()["data"]["task_id"] for r in responses]

task_ids = asyncio.run(generate_many(["topic1", "topic2", "topic3"]))
```

### Con retry y backoff

```python
import time

def create_with_retry(client, topic, max_retries=3):
    for attempt in range(max_retries):
        try:
            return client.create_video(topic=topic, mode="premium")
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 429:  # Queue full
                wait = 2 ** attempt
                print(f"Queue full, waiting {wait}s...")
                time.sleep(wait)
            else:
                raise
    raise RuntimeError("Max retries exceeded")
```

### Cost tracking

```python
total_cost = 0.0
for topic in topics:
    task_id = client.create_video(topic=topic, mode="premium")
    result = client.wait_for_task(task_id)
    if result.is_complete:
        costs = client.get_costs(task_id)
        total_cost += costs.get("total", 0)

print(f"Total: ${total_cost:.2f}")
```
