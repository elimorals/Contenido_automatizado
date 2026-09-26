"""Runtime de reasoners: `app.ai(...)` despacha a `core.llm_router`.

Esto ES el runtime (ADR-040). AgentField nunca llegó a ejecutarse — el SDK
instalado no exportaba `App` y todo caía siempre aquí — así que se retiró el
import, el adapter y el control-plane del compose.

`@reel.reasoner()` registra el reasoner por nombre; `app.ai(system=, user=,
schema=, temperature=)` es la firma heredada de reels-af.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


# ─────────────────────────────────────────────────────────────────────
# Runtime
# ─────────────────────────────────────────────────────────────────────


class _ReelRouter:
    """Router stub. Mantiene compatibilidad con `@reel.reasoner()` de reels-af."""

    def __init__(self) -> None:
        self._reasoners: dict[str, Callable[..., Any]] = {}

    def reasoner(self, name: str | None = None) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """Decorador para registrar un reasoner. No-op si no hay bus real."""

        def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
            self._reasoners[name or fn.__name__] = fn
            return fn

        return decorator

    def get(self, name: str) -> Callable[..., Any] | None:
        return self._reasoners.get(name)


class _AppStub:
    """Stub que despacha `.ai()` a `core.llm_router.complete_structured(...)`.

    Si el router no está disponible, lanza NotImplementedError — el código se
    importa sin errores pero falla ruidosamente si alguien intenta correr un
    reasoner sin haber cableado el LLM.

    Resolución del provider:
    1. `provider` kwarg explícito en la llamada
    2. `default_provider` del stub (settable vía `app.default_provider = "..."`)
    3. `llm.default_provider_premium` de config.toml / env
    4. "openrouter" (default histórico de reels-af)

    El paso 3 antes no existía: el stub hardcodeaba "openrouter", así que un
    repo configurado con `default_provider_premium = "openai"` corría el modo
    express bien y el DAG premium le fallaba con "[openrouter] api_key is
    required" — un error que apunta a un provider que el usuario nunca eligió.
    """

    def __init__(self, default_provider: str = "") -> None:
        self._explicit_provider = default_provider

    @property
    def default_provider(self) -> str:
        if self._explicit_provider:
            return self._explicit_provider
        try:
            from shared.config import load_config

            return load_config().llm_default_provider_premium or "openrouter"
        except Exception:  # noqa: BLE001 — sin config utilizable, default histórico
            return "openrouter"

    @default_provider.setter
    def default_provider(self, value: str) -> None:
        self._explicit_provider = value

    async def ai(
        self,
        *,
        system: str,
        user: str,
        schema: type[T],
        temperature: float = 0.7,
        max_tokens: int = 2000,
        provider: str | None = None,
        model_name: str | None = None,
        **kwargs: Any,
    ) -> T:
        """Despacha a `core.llm_router.complete_structured(...)`.

        Mantiene la firma kwargs-only que usa reels-af original (system=, user=,
        schema=, temperature=).
        """
        try:
            from core.llm_router import complete_structured
        except Exception as exc:  # pragma: no cover
            raise NotImplementedError(
                "core.llm_router no está disponible. Asegúrate de tener "
                "configurado al menos un provider en config.toml o .env."
            ) from exc

        return await complete_structured(  # type: ignore[no-any-return]
            prompt=user,
            schema=schema,
            provider=provider or self.default_provider,
            system=system,
            temperature=temperature,
            max_tokens=max_tokens,
            model_name=model_name,
            **kwargs,
        )


# ─────────────────────────────────────────────────────────────────────
# Exports públicos
# ─────────────────────────────────────────────────────────────────────


app = _AppStub()
reel = _ReelRouter()


__all__ = ["app", "reel"]
