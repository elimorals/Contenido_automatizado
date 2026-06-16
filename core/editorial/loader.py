"""Loader cacheado de la capa editorial.

Lee `editorial/` (al lado de `core/`, `apps/`, etc.) y arma un `EditorialRegistry`
con todo: brand-voice, pillars (por id), facts, audiences, platforms, events.

Cache global con invalidación manual (`reload_editorial()`). Mismo patrón que
`load_config()`.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from loguru import logger
from pydantic import BaseModel, Field

from shared.schemas import (
    Audience,
    DistributionPlatform,
    FactsDocument,
    LocalEvent,
    Pillar,
    PlatformSpec,
    BrandVisualConfig,
    VideoAspect,
    VideoDurationSpec,
)

# Búsqueda upward: si el cwd está dentro del proyecto, encontramos editorial/.
_EDITORIAL_DIRNAME = "editorial"


def _find_editorial_root(start: Path | None = None) -> Path | None:
    start = start or Path.cwd()
    for parent in [start, *start.parents]:
        candidate = parent / _EDITORIAL_DIRNAME
        if candidate.is_dir():
            return candidate
    return None


class EditorialRegistry(BaseModel):
    """Snapshot inmutable de toda la capa editorial."""

    root: Path
    brand_voice_md: str = ""
    pillars: dict[str, Pillar] = Field(default_factory=dict)
    pillar_docs: dict[str, str] = Field(default_factory=dict)  # id → md content
    audiences: dict[str, Audience] = Field(default_factory=dict)
    platforms: dict[DistributionPlatform, PlatformSpec] = Field(default_factory=dict)
    facts: FactsDocument = Field(default_factory=FactsDocument)
    local_events: list[LocalEvent] = Field(default_factory=list)
    brand_visual: dict[str, BrandVisualConfig] = Field(default_factory=dict)

    model_config = {"arbitrary_types_allowed": True}

    def has_pillar(self, pillar_id: str) -> bool:
        return pillar_id in self.pillars

    def has_audience(self, audience_id: str) -> bool:
        return audience_id in self.audiences

    def get_pillar_doc(self, pillar_id: str) -> str:
        return self.pillar_docs.get(pillar_id, "")

    def get_visual_for_tenant(self, tenant_id: str) -> BrandVisualConfig | None:
        """Devuelve la config visual del tenant, o None si no registrado.

        Fallback strategy: si `tenant_id` no existe, intenta `default`. Si
        tampoco, devuelve None y el caller usa los defaults del config TOML.
        """
        if tenant_id in self.brand_visual:
            return self.brand_visual[tenant_id]
        if "default" in self.brand_visual:
            return self.brand_visual["default"]
        return None


def _read_text(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _read_json(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8"))


def _load_pillars(root: Path) -> tuple[dict[str, Pillar], dict[str, str]]:
    pillars_dir = root / "pillars"
    pillars: dict[str, Pillar] = {}
    docs: dict[str, str] = {}
    if not pillars_dir.is_dir():
        return pillars, docs
    for md in sorted(pillars_dir.glob("*.md")):
        pid = md.stem
        content = _read_text(md)
        # Extraer primer H1 o usar el id como label
        label = pid.replace("-", " ").capitalize()
        first_line = next((ln for ln in content.splitlines() if ln.strip()), "")
        if first_line.startswith("# "):
            stripped = first_line[2:].strip()
            # "Pilar: X" → "X"
            label = stripped.split(":", 1)[-1].strip() or label
        # Description = primer párrafo después del H1 (best effort)
        description = ""
        body_lines = [ln for ln in content.splitlines() if not ln.startswith("#")]
        for ln in body_lines:
            if ln.strip():
                description = ln.strip()[:400]
                break
        try:
            pillars[pid] = Pillar(id=pid, label=label, description=description)
            docs[pid] = content
        except ValueError as e:
            logger.warning(f"[editorial] pillar {pid} inválido: {e}")
    return pillars, docs


def _load_audiences(root: Path) -> dict[str, Audience]:
    path = root / "audiences.json"
    if not path.is_file():
        return {}
    raw = _read_json(path)
    items = raw.get("audiences", [])
    out: dict[str, Audience] = {}
    for item in items:
        try:
            a = Audience(**item)
            out[a.id] = a
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[editorial] audience inválido: {e}")
    return out


def _load_platforms(root: Path) -> dict[DistributionPlatform, PlatformSpec]:
    path = root / "platforms.json"
    if not path.is_file():
        return {}
    raw = _read_json(path)
    items = raw.get("platforms", {})
    out: dict[DistributionPlatform, PlatformSpec] = {}
    for key, val in items.items():
        try:
            # Coercion: aspect_ratio puede venir como string ("9:16")
            ar_raw = val.get("aspect_ratio", "9:16")
            ar = VideoAspect(ar_raw) if isinstance(ar_raw, str) else ar_raw
            dur_raw = val.get("video_duration_s", {})
            dur = VideoDurationSpec(**dur_raw)
            crec = tuple(val.get("caption_recommended_chars", [1, 1]))
            spec = PlatformSpec(
                id=DistributionPlatform(val["id"]),
                aspect_ratio=ar,
                video_duration_s=dur,
                caption_max_chars=int(val["caption_max_chars"]),
                caption_recommended_chars=crec,  # type: ignore[arg-type]
                hashtags_min=int(val.get("hashtags_min", 0)),
                hashtags_max=int(val.get("hashtags_max", 0)),
                notes=val.get("notes", ""),
            )
            out[spec.id] = spec
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[editorial] platform {key} inválido: {e}")
    return out


def _load_facts(root: Path) -> FactsDocument:
    path = root / "facts.json"
    if not path.is_file():
        return FactsDocument()
    raw = _read_json(path)
    raw.pop("$schema", None)
    try:
        return FactsDocument(**raw)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[editorial] facts.json inválido: {e}")
        return FactsDocument()


def _load_events(root: Path) -> list[LocalEvent]:
    path = root / "local-events.json"
    if not path.is_file():
        return []
    raw = _read_json(path)
    items = raw.get("events", [])
    out: list[LocalEvent] = []
    for item in items:
        try:
            out.append(LocalEvent(**item))
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[editorial] event inválido: {e}")
    return out


def _load_brand_visual(root: Path) -> dict[str, BrandVisualConfig]:
    """Carga `editorial/brand-visual.json` → {tenant_id: BrandVisualConfig}."""
    path = root / "brand-visual.json"
    if not path.is_file():
        return {}
    raw = _read_json(path)
    items = raw.get("tenants", [])
    out: dict[str, BrandVisualConfig] = {}
    for item in items:
        try:
            bv = BrandVisualConfig(**item)
            out[bv.tenant_id] = bv
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[editorial] brand-visual entry inválido: {e}")
    return out


@lru_cache(maxsize=1)
def load_editorial(root: Path | str | None = None) -> EditorialRegistry:
    """Carga la capa editorial con cache. Llamar `reload_editorial()` para invalidar."""
    resolved = Path(root) if root else _find_editorial_root()
    if resolved is None:
        # No hay editorial/ — devolvemos vacío (compat con setups sin marca)
        logger.debug("[editorial] no se encontró editorial/ — usando registry vacío")
        return EditorialRegistry(root=Path.cwd())

    bv = resolved / "brand-voice.md"
    brand_voice = _read_text(bv) if bv.is_file() else ""

    pillars, pillar_docs = _load_pillars(resolved)
    audiences = _load_audiences(resolved)
    platforms = _load_platforms(resolved)
    facts = _load_facts(resolved)
    events = _load_events(resolved)
    brand_visual = _load_brand_visual(resolved)

    registry = EditorialRegistry(
        root=resolved,
        brand_voice_md=brand_voice,
        pillars=pillars,
        pillar_docs=pillar_docs,
        audiences=audiences,
        platforms=platforms,
        facts=facts,
        local_events=events,
        brand_visual=brand_visual,
    )
    logger.debug(
        f"[editorial] cargado: {len(pillars)} pilares, "
        f"{len(audiences)} audiencias, {len(platforms)} platforms, "
        f"{len(facts.verified_facts)} facts, {len(events)} events, "
        f"{len(brand_visual)} brand-visual tenants"
    )
    return registry


def reload_editorial(root: Path | str | None = None) -> EditorialRegistry:
    """Fuerza recarga (invalida cache de load_editorial)."""
    load_editorial.cache_clear()
    return load_editorial(root)


# =============================================================================
# Helper para hunters: bloque anti-alucinación
# =============================================================================


def facts_anti_hallucination_block(reg: EditorialRegistry | None = None) -> str:
    """Genera el bloque que se inyecta al system prompt de los hunters.

    Llena los slots con:
    - Rules MUST/MUST_NOT de facts.json
    - Listado de people verificados (name + years + field)
    - Listado de studies verificados (authors + year + finding)
    - Listado de facts verificados (claim + source)

    Si facts.json está vacío, devuelve un bloque GENERIC que solo dice
    "no inventes números ni nombres a menos que sean conocimiento público
    bien documentado".
    """
    r = reg or load_editorial()
    f = r.facts

    if not (f.verified_facts or f.verified_people or f.verified_studies):
        return _GENERIC_ANTI_HALLUCINATION

    parts = ["VERIFIED FACTS (cite ONLY from these — never invent numbers/names/years):"]

    if f.verified_people:
        parts.append("\nPEOPLE:")
        for p in f.verified_people:
            ya = f" ({p.years_active})" if p.years_active else ""
            parts.append(f"  • {p.name}{ya} — {p.field}: {p.relevance}")

    if f.verified_studies:
        parts.append("\nSTUDIES:")
        for s in f.verified_studies:
            authors = ", ".join(s.authors[:3])
            n = f", n={s.sample_size}" if s.sample_size else ""
            j = f" [{s.journal}]" if s.journal else ""
            parts.append(
                f"  • {authors} ({s.year}){j}{n}: {s.key_finding}"
            )

    if f.verified_facts:
        parts.append("\nFACTS:")
        for fact in f.verified_facts:
            yr = f" ({fact.year})" if fact.year else ""
            src = f" — {fact.source}" if fact.source else ""
            parts.append(f"  • [{fact.id}] {fact.claim}{yr}{src}")

    # Rules
    rules = f.rules_for_hunters or {}
    must = rules.get("must", [])
    must_not = rules.get("must_not", [])
    if must or must_not:
        parts.append("\nRULES:")
        for r_ in must:
            parts.append(f"  MUST: {r_}")
        for r_ in must_not:
            parts.append(f"  MUST NOT: {r_}")
    else:
        parts.append("\n" + _GENERIC_ANTI_HALLUCINATION)

    return "\n".join(parts)


_GENERIC_ANTI_HALLUCINATION = """ANTI-HALLUCINATION RULES:
  • Do NOT invent specific years, numbers, sample sizes, or names of people unless they are well-documented public knowledge (Wikipedia-tier).
  • When citing a person, year, or stat: it must be checkable. If unsure, drop the specific and use a broader formulation.
  • Phrases like "studies show", "researchers found", "most experts agree" are AUTO-REJECTED unless followed by a named source.
  • Prefer to omit a specific than to invent one."""
