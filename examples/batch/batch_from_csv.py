"""Batch generador desde CSV con polling concurrente.

Uso:
    python batch_from_csv.py topics.csv --mode express
    python batch_from_csv.py topics.csv --mode premium --concurrent 3
"""
from __future__ import annotations

import argparse
import csv
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# Agregar el dir python/ al path para importar client
sys.path.insert(0, str(Path(__file__).parent.parent / "python"))
from client import ContenidoClient, TaskResult


def process_one(client: ContenidoClient, topic: str, mode: str, aspect: str) -> dict:
    """Encola un topic y espera su resultado."""
    try:
        task_id = client.create_video(
            topic=topic,
            mode=mode,
            aspect=aspect,
            visual_strategy="hybrid",
        )
        print(f"  ✓ Encolado: {topic!r} → {task_id[:8]}")
        result = client.wait_for_task(task_id, poll_interval_s=3.0, timeout_s=600.0)
        return {
            "topic": topic,
            "task_id": task_id,
            "state": "complete" if result.is_complete else "failed",
            "duration_s": result.timings_s.get("total", 0),
            "video": result.videos[0] if result.videos else None,
            "error": result.error,
        }
    except Exception as e:
        return {
            "topic": topic,
            "task_id": None,
            "state": "error",
            "error": str(e),
        }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("csv_file", type=Path)
    parser.add_argument("--mode", default="express", choices=["express", "premium"])
    parser.add_argument("--aspect", default="9:16", choices=["9:16", "16:9", "1:1"])
    parser.add_argument("--concurrent", type=int, default=2, help="Tasks paralelas")
    parser.add_argument("--api-url", default=None)
    args = parser.parse_args()

    # Leer topics
    if not args.csv_file.exists():
        print(f"✗ CSV no existe: {args.csv_file}", file=sys.stderr)
        return 1

    topics = []
    with args.csv_file.open() as f:
        reader = csv.DictReader(f)
        for row in reader:
            topic = row.get("topic", "").strip()
            if topic:
                topics.append(topic)

    if not topics:
        print(f"✗ CSV vacío", file=sys.stderr)
        return 1

    print(f"→ Procesando {len(topics)} topics (concurrent={args.concurrent})\n")
    start = time.time()

    results = []
    with ContenidoClient(args.api_url) as client:
        with ThreadPoolExecutor(max_workers=args.concurrent) as pool:
            futures = {
                pool.submit(process_one, client, t, args.mode, args.aspect): t
                for t in topics
            }
            for fut in as_completed(futures):
                results.append(fut.result())

    elapsed = time.time() - start

    # Reporte
    completed = sum(1 for r in results if r["state"] == "complete")
    failed = len(results) - completed
    total_duration = sum(r.get("duration_s", 0) for r in results)

    print(f"\n=== Resumen ===")
    print(f"Total topics: {len(topics)}")
    print(f"  ✓ Completados: {completed}")
    print(f"  ✗ Fallidos: {failed}")
    print(f"Tiempo total (wall): {elapsed:.1f}s")
    print(f"Tiempo total (pipeline): {total_duration:.1f}s")
    print(f"Speedup por concurrency: {total_duration / elapsed:.1f}×")

    # Detalle de fallidos
    if failed:
        print(f"\n=== Fallidos ===")
        for r in results:
            if r["state"] != "complete":
                print(f"  {r['topic']!r}: {r.get('error', 'unknown')}")

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
