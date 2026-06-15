"""AgentField runtime adapter.

Expone `app` y `reel` (router) compatibles con el namespace de reels-af.

Si AgentField (`agentfield`) está instalado, importamos el `app` real desde
el módulo central del proyecto. Si no, exponemos un stub minimalista que
permite registrar reasoners (decorador `@reel.reasoner()`) y llamar a
`app.ai(system=..., user=..., schema=..., temperature=...)` despachando al
`core.llm_router`.

Esto sirve dos propósitos:
  1. Imports estables (`from core.narrative.runtime import app, reel`) sin
     romper si AgentField todavía no está cableado.
  2. Punto único donde el equipo puede swappear el bus (AgentField, raw
     LLM router, mock para tests) sin tocar los 8 reasoners.
"""

from __future__ import annotations

from typing import Any, Callable, Type, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


# ─────────────────────────────────────────────────────────────────────
# Intento de importar AgentField real
# ─────────────────────────────────────────────────────────────────────

try:
    # Convención: si el proyecto cablea AgentField, lo expone aquí.
    # Reemplaza este import por el real cuando se conecte.
    from agentfield import App as _AgentFieldApp  # type: ignore[import-not-found]

    _HAS_AGENTFIELD = True
except Exception:  # pragma: no cover - AgentField puede no estar instalado
    _AgentFieldApp = None  # type: ignore[assignment]
    _HAS_AGENTFIELD = False


# ─────────────────────────────────────────────────────────────────────
# Stub minimalista (fallback)
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
    3. "openrouter" (default reels-af)
    """

    def __init__(self, default_provider: str = "openrouter") -> None:
        self.default_provider = default_provider

    async def ai(
        self,
        *,
        system: str,
        user: str,
        schema: Type[T],
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


if _HAS_AGENTFIELD and _AgentFieldApp is not None:  # pragma: no cover
    app: Any = _AgentFieldApp()
    reel: Any = getattr(app, "reel", _ReelRouter())
else:
    app = _AppStub()
    reel = _ReelRouter()


__all__ = ["app", "reel"]
