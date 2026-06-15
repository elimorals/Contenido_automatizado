"""Genera un reel premium (DAG de 18 reasoners) desde un topic.

Uso:
    python examples/python/generate_premium.py "the placebo effect"
    USE_VEO=true python examples/python/generate_premium.py "fingerprints"
"""
from __future__ import annotations

import os
import sys

from client import ContenidoClient, TaskResult


def progress_callback(result: TaskResult) -> None:
    """Imprime las phases completadas (extraídas de timings_s)."""
    phases = [k for k, v in result.timings_s.items() if v > 0]
    last = phases[-1] if phases else "starting"
    print(f"  [progress={result.progress}%] last_phase={last}")


def main() -> int:
    topic = sys.argv[1] if len(sys.argv) > 1 else "the placebo effect"
    use_veo = os.getenv("USE_VEO", "false").lower() == "true"

    with ContenidoClient() as client:
        print(f"→ Generando reel PREMIUM para: {topic!r}")
        print(f"  use_veo={use_veo} (Veo i2v: {'$1.10' if use_veo else 'gratis ken-burns'})")

        task_id = client.create_video(
            topic=topic,
            mode="premium",
            aspect="9:16",
            visual_strategy="hybrid",
            use_veo=use_veo,
            subtitle_style="word_burst",
        )
        print(f"  task_id: {task_id}\n")

        result = client.wait_for_task(
            task_id,
            poll_interval_s=2.0,
            timeout_s=600.0,
            on_progress=progress_callback,
        )

        if result.is_complete:
            print(f"\n✓ Complete in {result.timings_s.get('total', 0):.1f}s")
            print(f"\n--- Output ---")
            print(f"Video: {result.videos[0] if result.videos else 'N/A'}")
            print(f"\n--- Timings ---")
            for phase, t in sorted(result.timings_s.items(), key=lambda x: -x[1]):
                if isinstance(t, (int, float)):
                    print(f"  {phase:20s} {t:6.2f}s")
            if result.cost_breakdown:
                print(f"\n--- Costs ---")
                for k, v in result.cost_breakdown.items():
                    print(f"  {k:20s} ${v:.4f}")
            return 0
        else:
            print(f"\n✗ Failed: {result.error}")
            return 1


if __name__ == "__main__":
    sys.exit(main())
