"""AgentField adapter — wrapper sobre el control-plane.

Si `agentfield` está disponible, despachamos al broker (DAG distribuido).
Si no, fallback local: ejecutamos los reasoners en proceso con
`asyncio.gather`, capturando exceptions por reasoner.

Cada dispatch trackea timings por phase y los retorna para guardarse en
`TaskInfo.timings_s` por el caller.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Awaitable, Callable

from loguru import logger

from shared.config import Config, load_config

# El SDK de agentfield puede no estar instalado en CI ligero.
try:
    from agentfield import Agent  # type: ignore[import-not-found]

    _AGENTFIELD_AVAILABLE = True
except ImportError:  # pragma: no cover
    Agent = None  # type: ignore[assignment,misc]
    _AGENTFIELD_AVAILABLE = False


Reasoner = Callable[..., Awaitable[Any]]


class DispatchResult:
    """Resultado de un dispatch de DAG con timing tracking."""

    def __init__(
        self,
        outputs: dict[str, Any],
        timings_s: dict[str, float],
        errors: dict[str, Exception],
    ) -> None:
        self.outputs = outputs
        self.timings_s = timings_s
        self.errors = errors

    @property
    def success(self) -> bool:
        return not self.errors


class AgentFieldAdapter:
    """Adapter con dos modos: remoto (AgentField broker) o local fallback."""

    def __init__(self, config: Config | None = None) -> None:
        self._cfg = config or load_config()
        self._agent: Any = None
        self._connected = False
        self._mode: str = "local"  # "remote" | "local"

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def connected(self) -> bool:
        return self._connected

    async def connect(self, server_url: str | None = None) -> None:
        """Conecta al broker AgentField. Si SDK no disponible → modo local."""
        if not _AGENTFIELD_AVAILABLE:
            logger.info("agentfield SDK no disponible — usando dispatch local")
            self._mode = "local"
            self._connected = True
            return
        url = server_url or self._cfg.app.agentfield_server
        try:
            # Crear un Agent client (sin reasoners — sólo para dispatch).
            self._agent = Agent(  # type: ignore[misc]
                node_id=f"{self._cfg.app.agentfield_node_id}-client",
                agentfield_server=url,
                version="1.0.0",
                description="Contenido orchestration client",
            )
            self._mode = "remote"
            self._connected = True
            logger.info(f"AgentField conectado a {url}")
        except Exception as e:
            logger.warning(
                f"AgentField connect falló ({e}) — fallback a dispatch local"
            )
            self._mode = "local"
            self._connected = True

    async def dispatch_dag(
        self,
        reasoners: list[tuple[str, Reasoner, dict[str, Any]]],
        timeout_s: float | None = None,
    ) -> DispatchResult:
        """Despacha una lista de reasoners en paralelo.

        Args:
            reasoners: lista de tuples `(phase_name, callable, kwargs)`. El
                callable es awaitable y recibe `**kwargs`. En modo remote,
                se invocaría el reasoner via broker (no soportado todavía —
                fallback a llamar local).
            timeout_s: timeout por reasoner. Si None, usa
                `cfg.app.agentfield_llm_call_timeout`.

        Returns:
            `DispatchResult` con outputs y timings por phase.
        """
        if not self._connected:
            await self.connect()
        timeout = timeout_s or float(self._cfg.app.agentfield_llm_call_timeout)

        outputs: dict[str, Any] = {}
        timings_s: dict[str, float] = {}
        errors: dict[str, Exception] = {}

        async def _run(phase: str, fn: Reasoner, kwargs: dict[str, Any]) -> None:
            start = time.perf_counter()
            try:
                # Aún en modo remote, si no tenemos invoker remoto registrado
                # ejecutamos local. El plumbing remoto se completa cuando los
                # reasoners estén registrados en el broker (Ola 4).
                result = await asyncio.wait_for(fn(**kwargs), timeout=timeout)
                outputs[phase] = result
            except Exception as e:
                logger.exception(f"reasoner {phase} falló: {e}")
                errors[phase] = e
            finally:
                timings_s[phase] = time.perf_counter() - start

        await asyncio.gather(
            *[_run(name, fn, kw) for name, fn, kw in reasoners],
            return_exceptions=False,  # cada _run captura su propio error
        )
        return DispatchResult(outputs=outputs, timings_s=timings_s, errors=errors)

    async def disconnect(self) -> None:
        """Cierra el cliente. No-op en modo local."""
        if self._agent is not None:
            close = getattr(self._agent, "close", None)
            if close is not None:
                try:
                    res = close()
                    if asyncio.iscoroutine(res):
                        await res
                except Exception as e:  # pragma: no cover
                    logger.warning(f"error closing AgentField agent: {e}")
        self._agent = None
        self._connected = False
