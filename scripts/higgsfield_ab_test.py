"""A/B harness para comparar Higgsfield DoP vs Veo per-beat.

Genera N reels desde una lista de topics; para cada beat genera AMBOS clips
(Veo + Higgsfield DoP) sobre el MISMO first frame, y registra en un CSV:

    topic, beat_idx, motion_hint, provider, cost_usd, latency_s,
    clip_path, frame_path, soul_id, effect

Después tú (o tu equipo) revisas los clips lado-a-lado y rellenas dos columnas
extra (`quality_vote_1_5`, `motion_fidelity_1_5`) — el resumen final usa esos
votos + la latencia/costo para emitir el verdict go/no-go.

Uso:
    # 25 topics × 2 providers × ~5 beats/topic ≈ 250 clips
    uv run python scripts/higgsfield_ab_test.py \
        --topics scripts/higgsfield_ab_topics.csv \
        --out-dir ./output/ab_higgsfield \
        --max-topics 25

    # Quick smoke test (1 topic, 3 beats)
    uv run python scripts/higgsfield_ab_test.py --quick

Salidas:
    {out_dir}/runs.csv              — fila por beat-provider
    {out_dir}/<topic_slug>/         — clips de ese topic
    {out_dir}/summary.md            — auto-resumen con totals

Notas:
    - Requiere OPENROUTER_API_KEY (Gemini Image first frames + Veo)
    - Requiere HIGGSFIELD_CREDENTIALS para la rama HF
    - Si una de las dos APIs falta, esa columna queda en blanco — no aborta
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger

# Asegurar import desde la raíz del repo
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from core.visual.generation import (  # noqa: E402
    GeminiImageGenerator,
    HiggsfieldDopGenerator,
    VeoGenerator,
    VisualGenerationError,
)
from shared.config import load_config  # noqa: E402
from shared.schemas import (  # noqa: E402
    Beat,
    BeatRole,
    BeatVisual,
    HiggsfieldPreset,
    MotionHint,
)


# =============================================================================
# Beat templates por topic (para no depender de los 18 reasoners en el A/B)
# =============================================================================

# Cada topic genera 3 beats representativos: hook (zoom_in/orbit), mechanism
# (pan o tilt), payoff (zoom_out o static). El usuario puede sustituir esto
# llamando al endpoint /narratives si quiere beats reales.

_DEFAULT_TOPICS: list[str] = [
    "the placebo effect",
    "why deep ocean creatures glow",
    "the science of yawning",
    "how memories rewrite themselves",
    "the secret life of fungi networks",
    "why we forget our dreams",
    "the math of murmuration",
    "how birds navigate by quantum compass",
    "the neuroscience of laughter",
    "why ice is slippery",
    "the chemistry of fear",
    "how octopuses think with their arms",
    "why the moon looks bigger near the horizon",
    "the genetics of identical twins differences",
    "how plants count time",
    "the physics of glass shattering",
    "why we cry tears of joy",
    "the strange case of cargo cults",
    "the math behind perfect heists",
    "why nobody knows how bicycles work",
    "the discovery of dark matter",
    "the origin of the alphabet",
    "why honeybees defy aerodynamics",
    "the lost city of Pavlopetri",
    "how aspirin actually works",
]


def _beats_for_topic(topic: str) -> tuple[list[Beat], list[BeatVisual]]:
    """Construye 3 beats canónicos con motion hints diversos para el A/B.

    HOOK    → zoom_in / orbit_360 (energía inicial)
    MECHANISM → pan_left / dolly_zoom (revelación)
    PAYOFF  → zoom_out / static    (cierre)
    """
    hook_beat = Beat(
        idx=0, role=BeatRole.HOOK, text=f"opening hook about {topic}",
        target_duration_s=4.0, veo_duration=4,
    )
    mech_beat = Beat(
        idx=1, role=BeatRole.MECHANISM, text=f"mechanism explaining {topic}",
        target_duration_s=6.0, veo_duration=6,
    )
    pay_beat = Beat(
        idx=2, role=BeatRole.PAYOFF, text=f"payoff closing {topic}",
        target_duration_s=4.0, veo_duration=4,
    )

    hook_v = BeatVisual(
        image_prompt=f"cinematic wide shot illustrating: {topic}",
        motion_hint=MotionHint.SLOW_ZOOM_IN,
        visual_anchor=topic,
        higgsfield_preset=HiggsfieldPreset.ORBIT_360,  # showcase preset cinematográfico
    )
    mech_v = BeatVisual(
        image_prompt=f"close-up reveal of a key detail of {topic}",
        motion_hint=MotionHint.PAN_LEFT,
        visual_anchor=f"{topic} detail",
        higgsfield_preset=HiggsfieldPreset.DOLLY_ZOOM_IN,  # showcase
    )
    pay_v = BeatVisual(
        image_prompt=f"warm closing frame summarizing {topic}",
        motion_hint=MotionHint.SLOW_ZOOM_OUT,
        visual_anchor=topic,
        higgsfield_preset=HiggsfieldPreset.SUPER_DOLLY_OUT,
    )
    return [hook_beat, mech_beat, pay_beat], [hook_v, mech_v, pay_v]


# =============================================================================
# Run record
# =============================================================================


@dataclass
class RunRecord:
    topic: str
    beat_idx: int
    role: str
    motion_hint: str
    higgsfield_preset: str
    provider: str            # 'veo' | 'higgsfield_dop'
    clip_path: str = ""
    frame_path: str = ""
    cost_usd: float = 0.0
    latency_s: float = 0.0
    status: str = "pending"  # 'ok' | 'failed' | 'skipped'
    error: str = ""
    # Para review humano (se llenan post-run):
    quality_vote_1_5: str = ""
    motion_fidelity_1_5: str = ""


_CSV_HEADER = [
    "topic", "beat_idx", "role", "motion_hint", "higgsfield_preset",
    "provider", "clip_path", "frame_path", "cost_usd", "latency_s",
    "status", "error", "quality_vote_1_5", "motion_fidelity_1_5",
]


def _slug(text: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in text.lower())[:40]


# =============================================================================
# Per-topic worker
# =============================================================================


async def _generate_for_topic(
    topic: str,
    out_root: Path,
    *,
    image_gen: GeminiImageGenerator,
    veo_enabled: bool,
    hf_enabled: bool,
) -> list[RunRecord]:
    """Genera 3 beats × 2 providers = 6 clips para un topic."""
    topic_dir = out_root / _slug(topic)
    topic_dir.mkdir(parents=True, exist_ok=True)
    beats, visuals = _beats_for_topic(topic)
    records: list[RunRecord] = []

    # === Paso 1: first frames compartidos (un solo cost de Gemini Image) ===
    frames: dict[int, Path | None] = {}
    for beat, visual in zip(beats, visuals):
        try:
            art = await image_gen.generate(
                beat, visual, "general", topic_dir,
            )
            frames[beat.idx] = art.first_frame_path
        except Exception as e:
            logger.error(f"[ab] {topic} beat {beat.idx} frame gen falló: {e}")
            frames[beat.idx] = None

    # === Paso 2: por cada beat, generar Veo y Higgsfield en paralelo ===
    veo_gen = VeoGenerator() if veo_enabled else None
    hf_gen = HiggsfieldDopGenerator() if hf_enabled else None

    for beat, visual in zip(beats, visuals):
        frame = frames.get(beat.idx)

        async def _run_provider(
            provider_name: str,
            gen: Any,
            cost_estimate: float,
        ) -> RunRecord:
            rec = RunRecord(
                topic=topic,
                beat_idx=beat.idx,
                role=beat.role.value,
                motion_hint=visual.motion_hint.value,
                higgsfield_preset=(
                    visual.higgsfield_preset.value if visual.higgsfield_preset else ""
                ),
                provider=provider_name,
                frame_path=str(frame) if frame else "",
            )
            if gen is None:
                rec.status = "skipped"
                rec.error = f"{provider_name} disabled or credentials missing"
                return rec
            if frame is None:
                rec.status = "skipped"
                rec.error = "first frame failed"
                return rec
            t0 = time.monotonic()
            try:
                art = await gen.generate(
                    beat=beat,
                    visual=visual,
                    content_mode="general",
                    out_dir=topic_dir,
                    first_frame_path=frame,
                )
                rec.clip_path = str(art.video_path) if art.video_path else ""
                rec.status = "ok" if art.video_path else "failed"
                rec.cost_usd = cost_estimate
            except VisualGenerationError as e:
                rec.status = "failed"
                rec.error = str(e)[:200]
            except Exception as e:  # noqa: BLE001
                rec.status = "failed"
                rec.error = f"unexpected: {type(e).__name__}: {e}"[:200]
            rec.latency_s = round(time.monotonic() - t0, 2)
            return rec

        # Lanzar en paralelo — son APIs distintas
        v_rec, h_rec = await asyncio.gather(
            _run_provider("veo", veo_gen, 0.30),
            _run_provider("higgsfield_dop", hf_gen, 0.20),
        )
        records.extend([v_rec, h_rec])
        logger.info(
            f"[ab] {topic} beat {beat.idx} "
            f"veo={v_rec.status} ({v_rec.latency_s}s) "
            f"hf={h_rec.status} ({h_rec.latency_s}s)"
        )

    return records


# =============================================================================
# CSV + summary
# =============================================================================


def _write_csv(records: list[RunRecord], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(_CSV_HEADER)
        for r in records:
            w.writerow([
                r.topic, r.beat_idx, r.role, r.motion_hint,
                r.higgsfield_preset, r.provider, r.clip_path, r.frame_path,
                f"{r.cost_usd:.4f}", f"{r.latency_s:.2f}", r.status, r.error,
                r.quality_vote_1_5, r.motion_fidelity_1_5,
            ])


def _write_summary(records: list[RunRecord], path: Path) -> None:
    by_provider: dict[str, dict[str, Any]] = {
        "veo": {"ok": 0, "failed": 0, "skipped": 0, "cost": 0.0, "latency": []},
        "higgsfield_dop": {"ok": 0, "failed": 0, "skipped": 0, "cost": 0.0, "latency": []},
    }
    for r in records:
        if r.provider not in by_provider:
            continue
        slot = by_provider[r.provider]
        slot[r.status] = slot.get(r.status, 0) + 1
        slot["cost"] += r.cost_usd
        if r.status == "ok":
            slot["latency"].append(r.latency_s)

    def _avg(xs: list[float]) -> str:
        return f"{sum(xs) / len(xs):.1f}s" if xs else "—"

    lines = [
        "# Higgsfield A/B Test — Summary",
        "",
        f"Total runs: {len(records)}",
        "",
        "| Provider | OK | Failed | Skipped | Total cost | Avg latency (OK) |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for prov, s in by_provider.items():
        lines.append(
            f"| {prov} | {s['ok']} | {s['failed']} | {s['skipped']} | "
            f"${s['cost']:.2f} | {_avg(s['latency'])} |"
        )
    lines += [
        "",
        "## Siguiente paso",
        "",
        "1. Abre cada par de clips (veo vs higgsfield_dop) en un viewer",
        "2. Llena las columnas `quality_vote_1_5` y `motion_fidelity_1_5` en runs.csv",
        "3. Vuelve a correr `python scripts/higgsfield_ab_test.py --score` (TODO)",
        "   para emitir verdict go/no-go basado en los votos.",
    ]
    path.write_text("\n".join(lines))


# =============================================================================
# CLI
# =============================================================================


async def _main_async(args: argparse.Namespace) -> int:
    cfg = load_config()

    # Sanity-check credenciales
    has_or = bool(os.getenv("OPENROUTER_API_KEY"))
    has_hf = bool(
        cfg.visual.higgsfield.credentials
        or (cfg.visual.higgsfield.key_id and cfg.visual.higgsfield.key_secret)
    )
    if not has_or:
        logger.error("OPENROUTER_API_KEY no configurada — frames + Veo no funcionarán")
    if not has_hf:
        logger.warning("Higgsfield credenciales ausentes — solo se generará la rama Veo")

    veo_enabled = has_or and not args.no_veo
    hf_enabled = has_hf and not args.no_higgsfield

    # Lista de topics
    topics: list[str]
    if args.topics and args.topics.exists():
        with args.topics.open() as f:
            topics = [line.strip() for line in f if line.strip() and not line.startswith("#")]
    else:
        topics = list(_DEFAULT_TOPICS)
    if args.quick:
        topics = topics[:1]
    elif args.max_topics:
        topics = topics[: args.max_topics]

    out_root = args.out_dir
    out_root.mkdir(parents=True, exist_ok=True)

    image_gen = GeminiImageGenerator()
    all_records: list[RunRecord] = []
    for i, topic in enumerate(topics, 1):
        logger.info(f"[ab] ({i}/{len(topics)}) topic={topic!r}")
        try:
            recs = await _generate_for_topic(
                topic, out_root,
                image_gen=image_gen,
                veo_enabled=veo_enabled,
                hf_enabled=hf_enabled,
            )
            all_records.extend(recs)
        except Exception as e:  # noqa: BLE001
            logger.exception(f"[ab] topic {topic!r} crashed: {e}")

    csv_path = out_root / "runs.csv"
    summary_path = out_root / "summary.md"
    _write_csv(all_records, csv_path)
    _write_summary(all_records, summary_path)
    logger.info(f"[ab] CSV escrito: {csv_path}")
    logger.info(f"[ab] Summary: {summary_path}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="Higgsfield vs Veo A/B harness")
    p.add_argument(
        "--topics", type=Path, default=None,
        help="CSV/TXT con un topic por línea (# = comentario)",
    )
    p.add_argument(
        "--out-dir", type=Path, default=Path("./output/ab_higgsfield"),
        help="Directorio para clips + CSV + summary",
    )
    p.add_argument(
        "--max-topics", type=int, default=None,
        help="Tope de topics (default: todos en la lista)",
    )
    p.add_argument(
        "--quick", action="store_true",
        help="Smoke test con 1 topic × 3 beats × 2 providers = 6 clips",
    )
    p.add_argument("--no-veo", action="store_true", help="Skip Veo branch")
    p.add_argument("--no-higgsfield", action="store_true", help="Skip Higgsfield branch")
    args = p.parse_args()
    return asyncio.run(_main_async(args))


if __name__ == "__main__":
    sys.exit(main())
