"""Costo por job: una sola tabla, un ledger, un tope.

Antes había 4 tablas de costo que no coincidían, Sora/OpenAI/LLM registraban
$0, no existía tope y un timeout seguido de fallback pagaba dos proveedores
por el mismo beat. Aquí:

- ``estimate_usd(source, seconds)``: la ÚNICA tabla de precios visuales.
- ``JobLedger``: lo que se gastó de verdad en el job (LLM + visual), con tope.
  Vive en un ContextVar, así que cualquier capa lo encuentra sin pasarlo a mano
  y sobrevive a ``asyncio.gather``.
- Circuit breaker de proceso: un 401/402/403 apaga ese proveedor hasta reiniciar.

Los precios son estimaciones (septiembre 2026) — verifícalos contra la página
del proveedor antes de fijar presupuestos finos.
"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field

from loguru import logger

from shared.schemas import VideoSource

# USD por clip/imagen (fijo) y USD por segundo de video (variable).
_PER_ITEM: dict[VideoSource, float] = {
    VideoSource.GEMINI_IMAGE: 0.04,
    VideoSource.OPENAI_IMAGE: 0.06,  # gpt-image-1 medium, 1024x1536
    VideoSource.HIGGSFIELD_SOUL: 0.05,
    VideoSource.HIGGSFIELD_EFFECT: 0.10,
    VideoSource.HIGGSFIELD_DOP: 0.30,  # DoP turbo, clip 5 s
    # MCP gasta créditos de la cuenta web; sin tarifa pública → mismo que DoP.
    VideoSource.HIGGSFIELD_MCP: 0.30,
    VideoSource.FAL: 0.25,
}
_PER_SECOND: dict[VideoSource, float] = {
    VideoSource.SORA: 0.10,  # sora-2 720p
    VideoSource.VEO: 0.18,  # Veo 3.1 Lite
    VideoSource.LIVE_AVATAR: 0.05,  # remote_http; local_cli ≈ 0
}


def estimate_usd(source: VideoSource, seconds: float = 0.0) -> float:
    """Costo estimado de UNA generación de ``source`` (0 para stock/local)."""
    return _PER_ITEM.get(source, 0.0) + _PER_SECOND.get(source, 0.0) * max(0.0, seconds)


def is_paid(source: VideoSource) -> bool:
    return source in _PER_ITEM or source in _PER_SECOND


class BudgetExceeded(RuntimeError):
    """El job llegó a su tope de gasto; no se lanza ninguna llamada pagada más."""


@dataclass
class JobLedger:
    limit_usd: float | None = None
    entries: list[tuple[str, str, float]] = field(default_factory=list)  # (categoría, qué, usd)

    @property
    def total_usd(self) -> float:
        return sum(e[2] for e in self.entries)

    def ensure(self, est_usd: float, what: str) -> None:
        """Levanta ``BudgetExceeded`` si ``est_usd`` rompería el tope."""
        if self.limit_usd is not None and self.total_usd + est_usd > self.limit_usd:
            raise BudgetExceeded(
                f"{what}: ${self.total_usd:.2f} gastado + ${est_usd:.2f} > tope ${self.limit_usd:.2f}"
            )

    def charge(self, category: str, what: str, usd: float) -> None:
        self.entries.append((category, what, round(usd, 6)))

    def breakdown(self) -> dict[str, float]:
        out: dict[str, float] = {}
        for cat, what, usd in self.entries:
            key = f"{cat}:{what}"
            out[key] = round(out.get(key, 0.0) + usd, 6)
        out["total_usd"] = round(self.total_usd, 6)
        return out


_ledger: ContextVar[JobLedger | None] = ContextVar("job_ledger", default=None)


def current_ledger() -> JobLedger | None:
    return _ledger.get()


@contextmanager
def job_budget(limit_usd: float | None) -> Iterator[JobLedger]:
    ledger = JobLedger(limit_usd=limit_usd)
    token = _ledger.set(ledger)
    try:
        yield ledger
    finally:
        _ledger.reset(token)


def ensure_budget(est_usd: float, what: str) -> None:
    if (ledger := _ledger.get()) is not None:
        ledger.ensure(est_usd, what)


def charge(category: str, what: str, usd: float) -> None:
    if usd and (ledger := _ledger.get()) is not None:
        ledger.charge(category, what, usd)


# ── circuit breaker ──────────────────────────────────────────────────
# ponytail: por proceso y sin expiración; si un proveedor se recarga en
# caliente hay que reiniciar el worker. Añadir TTL cuando moleste.
_tripped: set[VideoSource] = set()


def is_tripped(source: VideoSource) -> bool:
    return source in _tripped


def note_failure(source: VideoSource, exc: BaseException) -> None:
    """Apaga el proveedor si el error es de cuenta (auth/saldo), no transitorio."""
    msg = str(exc)
    if any(code in msg for code in ("401", "402", "403", "insufficient", "credits")):
        _tripped.add(source)
        logger.warning(f"[cost] {source.value} desactivado en este proceso: {msg[:120]}")


def reset_breaker() -> None:
    _tripped.clear()


def budgeted(fn):
    """Envuelve un productor async en su propio ledger (tope por episodio).

    Si ya hay un ledger activo (p.ej. el harness de series), se reutiliza.
    ponytail: el tope es por corrida; al reanudar desde checkpoint lo ya gastado
    en la corrida anterior no cuenta. Persistir el ledger en el checkpoint si importa.
    """
    import functools

    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        if _ledger.get() is not None:
            return await fn(*args, **kwargs)
        from shared.config import load_config

        with job_budget(load_config().budget.max_usd_per_episode) as ledger:
            try:
                return await fn(*args, **kwargs)
            finally:
                logger.info(f"[cost] {fn.__name__}: ${ledger.total_usd:.2f} gastado")

    return wrapper
