"""A/B test de narrativas: genera 3 ScriptDrafts para el mismo topic y compáralos.

Uso:
    python examples/python/narratives_ab_test.py "the placebo effect"
"""
from __future__ import annotations

import json
import sys

from client import ContenidoClient


def main() -> int:
    topic = sys.argv[1] if len(sys.argv) > 1 else "the placebo effect"
    n = 3

    with ContenidoClient() as client:
        print(f"→ Generando {n} narrativas para: {topic!r}\n")

        narratives = []
        for i in range(n):
            print(f"  [{i + 1}/{n}] Llamando /narratives...")
            try:
                data = client.generate_narrative(topic=topic, mode="premium")
                narratives.append(data)
            except Exception as e:
                print(f"    ✗ Falló: {e}")

        if not narratives:
            print("✗ Ninguna narrativa generada")
            return 1

        print(f"\n=== {len(narratives)} narrativas generadas ===\n")
        for i, n in enumerate(narratives, 1):
            winner = n.get("winner") or n.get("script_draft") or {}
            print(f"--- Narrativa #{i} ---")
            print(f"Hook: {winner.get('hook') or winner.get('tease', 'N/A')}")
            print(f"Payoff: {winner.get('payoff_line') or winner.get('payoff', 'N/A')}")
            if "composite_score" in n:
                print(f"Score: {n['composite_score']:.1f}/10")
                print(f"Why: {n.get('why', '')}")
            print()

        # Guardar todo el output para análisis
        out_file = "narratives_ab_test_output.json"
        with open(out_file, "w") as f:
            json.dump(narratives, f, indent=2, ensure_ascii=False)
        print(f"Output completo guardado en: {out_file}")

        return 0


if __name__ == "__main__":
    sys.exit(main())
