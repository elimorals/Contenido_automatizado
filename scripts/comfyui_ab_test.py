"""A/B harness: ComfyUI (brand LoRA) vs Gemini Image (default).

Genera el mismo prompt en ambos providers para N topics × M beats. Mide:
- identity_consistency: ¿se ve consistente cross-beats? (humano, 1-5)
- style_alignment: ¿respeta tu brand style_suffix? (humano, 1-5)
- cost_usd: real para ComfyUI (compute o managed), 0 para Gemini (en el pricing)
- latency_s: tiempo real de generación

Output:
- {out_dir}/runs.csv — fila por beat-provider (10 columnas)
- {out_dir}/summary.md — resumen para decisión go/no-go
- {out_dir}/<topic>/<provider>/beat-XX.jpg — clips lado-a-lado

Uso:
    uv run python scripts/comfyui_ab_test.py --quick
    uv run python scripts/comfyui_ab_test.py --topics 10 --tenant ruteo
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from loguru import logger

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from core.editorial import load_editorial  # noqa: E402
from core.visual.generation import GeminiImageGenerator  # noqa: E402
from core.visual.generation.base import VisualGenerationError  # noqa: E402
from core.visual.generation.comfy import ComfyUIGenerator  # noqa: E402
from shared.schemas import (  # noqa: E402
    Beat,
    BeatRole,
    BeatVisual,
    MotionHint,
)


_DEFAULT_TOPICS = [
    "el efecto placebo y por qué funciona",
    "qué hace especial al café de altura",
    "la neurociencia del aburrimiento",
    "por qué los hongos son tu pariente lejano",
    "el origen del alfabeto fenicio",
    "cómo los pulpos piensan con sus brazos",
    "la matemática detrás del murmullo de estorninos",
    "por qué olvidamos los sueños al despertar",
    "el descubrimiento accidental de la penicilina",
    "cómo funciona realmente la aspirina",
]


@dataclass
class RunRecord:
    topic: str
    beat_idx: int
    role: str
    provider: str  # 'comfyui' | 'gemini'
    tenant_id: str = ""
    workflow_id: str = ""
    workflow_version: str = ""
    output_path: str = ""
    status: str = "pending"  # 'ok' | 'failed'
    cost_usd: float = 0.0
    latency_s: float = 0.0
    error: str = ""
    # Llenar a mano post-run:
    identity_consistency_1_5: str = ""
    style_alignment_1_5: str = ""


_CSV_HEADER = [
    "topic", "beat_idx", "role", "provider", "tenant_id",
    "workflow_id", "workflow_version", "output_path",
    "status", "cost_usd", "latency_s", "error",
    "identity_consistency_1_5", "style_alignment_1_5",
]


def _slug(s: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in s.lower())[:40]


def _build_beat_set(topic: str) -> tuple[list[Beat], list[BeatVisual]]:
    """3 beats representativos por topic."""
    beats = [
        Beat(idx=0, role=BeatRole.HOOK, text="hook", target_duration_s=3.0, veo_duration=4),
        Beat(idx=1, role=BeatRole.MECHANISM, text="mechanism", target_duration_s=6.0, veo_duration=6),
        Beat(idx=2, role=BeatRole.PAYOFF, text="payoff", target_duration_s=3.0, veo_duration=4),
    ]
    visuals = [
        BeatVisual(
            image_prompt=f"cinematic hook image illustrating: {topic}",
            motion_hint=MotionHint.SLOW_ZOOM_IN, visual_anchor=topic,
        ),
        BeatVisual(
            image_prompt=f"close-up of key detail in: {topic}",
            motion_hint=MotionHint.PAN_LEFT, visual_anchor=topic,
        ),
        BeatVisual(
            image_prompt=f"closing frame summarizing: {topic}",
            motion_hint=MotionHint.SLOW_ZOOM_OUT, visual_anchor=topic,
        ),
    ]
    return beats, visuals


async def _generate_for_topic(
    topic: str,
    out_root: Path,
    *,
    tenant_id: str,
    comfy_enabled: bool,
    gemini_enabled: bool,
) -> list[RunRecord]:
    topic_dir = out_root / _slug(topic)
    topic_dir.mkdir(parents=True, exist_ok=True)
    beats, visuals = _build_beat_set(topic)

    # Reuse generators across beats (cheaper)
    comfy_gen = ComfyUIGenerator() if comfy_enabled else None
    gemini_gen = GeminiImageGenerator() if gemini_enabled else None
    # Forzar enabled si fue creado
    if comfy_gen is not None:
        comfy_gen.cfg = comfy_gen.cfg.model_copy(update={"enabled": True})

    records: list[RunRecord] = []

    for beat, visual in zip(beats, visuals):
        # Inyectar tenant en visual.soul_id (convención multi-tenant)
        v = visual.model_copy(update={"soul_id": tenant_id}) if tenant_id else visual

        async def _run_one(provider_name: str, gen) -> RunRecord:
            rec = RunRecord(
                topic=topic, beat_idx=beat.idx, role=beat.role.value,
                provider=provider_name, tenant_id=tenant_id,
            )
            if gen is None:
                rec.status = "skipped"
                rec.error = f"{provider_name} disabled"
                return rec
            sub_dir = topic_dir / provider_name
            sub_dir.mkdir(parents=True, exist_ok=True)
            t0 = time.monotonic()
            try:
                artifact = await gen.generate(
                    beat=beat, visual=v, content_mode="general", out_dir=sub_dir,
                )
                rec.output_path = str(artifact.first_frame_path or artifact.video_path or "")
                rec.status = "ok"
                if provider_name == "comfyui":
                    rec.workflow_id = getattr(gen, "last_job", None) and gen.last_job.workflow_id or ""
                    rec.workflow_version = getattr(gen, "last_workflow_version", "")
                    rec.cost_usd = gen._cost_estimate()
            except VisualGenerationError as e:
                rec.status = "failed"
                rec.error = str(e)[:200]
            except Exception as e:  # noqa: BLE001
                rec.status = "failed"
                rec.error = f"unexpected: {type(e).__name__}: {e}"[:200]
            rec.latency_s = round(time.monotonic() - t0, 2)
            return rec

        # Parallel para minimizar tiempo de muro
        c_rec, g_rec = await asyncio.gather(
            _run_one("comfyui", comfy_gen),
            _run_one("gemini", gemini_gen),
        )
        records.extend([c_rec, g_rec])
        logger.info(
            f"[ab] {topic} beat {beat.idx} "
            f"comfy={c_rec.status} ({c_rec.latency_s}s) "
            f"gemini={g_rec.status} ({g_rec.latency_s}s)"
        )

    return records


def _write_csv(records: list[RunRecord], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(_CSV_HEADER)
        for r in records:
            w.writerow([
                r.topic, r.beat_idx, r.role, r.provider, r.tenant_id,
                r.workflow_id, r.workflow_version, r.output_path,
                r.status, f"{r.cost_usd:.4f}", f"{r.latency_s:.2f}",
                r.error, r.identity_consistency_1_5, r.style_alignment_1_5,
            ])


def _write_summary(records: list[RunRecord], path: Path) -> None:
    by_p: dict[str, dict] = {
        "comfyui": {"ok": 0, "failed": 0, "skipped": 0, "cost": 0.0, "lat": []},
        "gemini": {"ok": 0, "failed": 0, "skipped": 0, "cost": 0.0, "lat": []},
    }
    for r in records:
        if r.provider not in by_p:
            continue
        slot = by_p[r.provider]
        slot[r.status] = slot.get(r.status, 0) + 1
        slot["cost"] += r.cost_usd
        if r.status == "ok":
            slot["lat"].append(r.latency_s)

    def _avg(xs: list[float]) -> str:
        return f"{sum(xs) / len(xs):.1f}s" if xs else "—"

    lines = [
        "# ComfyUI vs Gemini A/B Test — Summary", "",
        f"Total runs: {len(records)}", "",
        "| Provider | OK | Failed | Skipped | Total cost | Avg latency (OK) |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for prov, s in by_p.items():
        lines.append(
            f"| {prov} | {s['ok']} | {s['failed']} | {s['skipped']} | "
            f"${s['cost']:.2f} | {_avg(s['lat'])} |"
        )
    lines += [
        "", "## Siguiente paso",
        "1. Abre cada par de outputs (comfyui vs gemini) lado-a-lado",
        "2. Llena `identity_consistency_1_5` y `style_alignment_1_5` en runs.csv",
        "3. Si comfyui gana ≥4/5 promedio en identity → mantén ComfyUI prefer_for_brand_frames=true",
        "4. Si gana <3/5 → tu LoRA necesita más training o el brand_visual.style_suffix no es claro",
    ]
    path.write_text("\n".join(lines))


async def _main(args: argparse.Namespace) -> int:
    out_root = args.out_dir
    out_root.mkdir(parents=True, exist_ok=True)

    # Verificar editorial brand-visual tiene el tenant
    reg = load_editorial()
    bv = reg.get_visual_for_tenant(args.tenant)
    if bv is None:
        logger.warning(
            f"tenant '{args.tenant}' no encontrado en editorial/brand-visual.json — "
            f"se usará default"
        )
    elif not bv.lora_name:
        logger.warning(
            f"tenant '{args.tenant}' no tiene LoRA configurada — "
            f"el test medirá ComfyUI sin LoRA vs Gemini (poco interesante)"
        )

    topics = list(_DEFAULT_TOPICS)
    if args.quick:
        topics = topics[:1]
    elif args.topics:
        topics = topics[: args.topics]

    all_records: list[RunRecord] = []
    for i, t in enumerate(topics, 1):
        logger.info(f"[ab] ({i}/{len(topics)}) topic={t!r}")
        try:
            recs = await _generate_for_topic(
                t, out_root,
                tenant_id=args.tenant,
                comfy_enabled=not args.no_comfy,
                gemini_enabled=not args.no_gemini,
            )
            all_records.extend(recs)
        except Exception as e:  # noqa: BLE001
            logger.exception(f"[ab] topic {t!r} crashed: {e}")

    _write_csv(all_records, out_root / "runs.csv")
    _write_summary(all_records, out_root / "summary.md")
    logger.info(f"[ab] CSV: {out_root / 'runs.csv'}")
    logger.info(f"[ab] Summary: {out_root / 'summary.md'}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="ComfyUI vs Gemini AB harness")
    p.add_argument("--tenant", default="default", help="tenant_id (editorial)")
    p.add_argument("--topics", type=int, default=None, help="cap de topics")
    p.add_argument("--out-dir", type=Path, default=Path("./output/ab_comfyui"))
    p.add_argument("--quick", action="store_true", help="1 topic × 3 beats × 2 providers")
    p.add_argument("--no-comfy", action="store_true")
    p.add_argument("--no-gemini", action="store_true")
    args = p.parse_args()
    return asyncio.run(_main(args))


if __name__ == "__main__":
    sys.exit(main())
