"""Genera un reel express y muestra el progreso.

Uso:
    python examples/python/generate_express.py "Spring flowers"
"""
from __future__ import annotations

import sys

from client import ContenidoClient, TaskResult


def progress_callback(result: TaskResult) -> None:
    print(f"  state={result.state} progress={result.progress}% timings={list(result.timings_s.keys())}")


def main() -> int:
    subject = sys.argv[1] if len(sys.argv) > 1 else "Spring flowers in Tokyo"

    with ContenidoClient() as client:
        # Health check
        health = client.health()
        print(f"API status: {health.get('status')}")

        # Crear task
        print(f"\n→ Generando reel express para: {subject!r}")
        task_id = client.create_video(
            subject=subject,
            mode="express",
            aspect="9:16",
            voice_name="en-US-AvaNeural-Female",
        )
        print(f"  task_id: {task_id}")

        # Esperar
        result = client.wait_for_task(
            task_id,
            poll_interval_s=2.0,
            timeout_s=600.0,
            on_progress=progress_callback,
        )

        if result.is_complete:
            print(f"\n✓ Done in {result.timings_s.get('total', 0):.1f}s")
            print(f"  Video: {result.videos[0] if result.videos else 'N/A'}")
            print(f"  Timings: {result.timings_s}")
            return 0
        else:
            print(f"\n✗ Failed: {result.error}")
            return 1


if __name__ == "__main__":
    sys.exit(main())
