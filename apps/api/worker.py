"""Worker que consume tasks de la cola (memory|Redis) y ejecuta el pipeline.

Múltiples instancias pueden correr en paralelo (con `MAX_CONCURRENT_TASKS`
workers). Cada uno saca una task por vez y la ejecuta hasta completar/fallar.

Resiliencia:
- Graceful shutdown con SIGTERM/SIGINT.
- Si `dequeue` falla (Redis down), reintenta con backoff exponencial capped a
  30s. No crashea el worker.
- Si una task falla, `run_pipeline` ya marca FAILED en state; el worker solo
  loggea y sigue al siguiente item.
"""
from __future__ import annotations

import asyncio
import os
import signal
import sys

from loguru import logger

from apps.api.pipeline import run_pipeline
from orchestration.queue import get_task_manager
from orchestration.state import get_state_manager
from shared.config import load_config


async def main() -> None:
    cfg = load_config()
    worker_id = os.getenv("WORKER_ID", f"worker-{os.getpid()}")
    logger.info(
        f"[{worker_id}] starting — env={cfg.app.env}, "
        f"redis={cfg.app.enable_redis}, "
        f"max_concurrent={cfg.app.max_concurrent_tasks}"
    )

    queue = get_task_manager(cfg)
    state = get_state_manager(cfg)

    stop_event = asyncio.Event()

    def _signal_handler(signum: int, _frame: object) -> None:
        logger.info(f"[{worker_id}] signal {signum} received — draining, will exit")
        stop_event.set()

    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)

    backoff_s = 1.0
    max_backoff_s = 30.0

    try:
        while not stop_event.is_set():
            try:
                item = await queue.dequeue(timeout=5.0)
            except Exception as e:
                # Probable Redis caída. Backoff y reintento sin matar el worker.
                logger.warning(
                    f"[{worker_id}] dequeue error (backoff={backoff_s:.1f}s): {e}"
                )
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=backoff_s)
                    break  # signal vino durante el backoff
                except asyncio.TimeoutError:
                    pass
                backoff_s = min(backoff_s * 2, max_backoff_s)
                continue

            # Dequeue ok → reseteamos backoff.
            backoff_s = 1.0

            if item is None:
                # Timeout natural; loop para chequear stop_event.
                continue

            task_id, params = item
            logger.info(f"[{worker_id}] picked up task {task_id[:8]}…")

            try:
                await run_pipeline(params, task_id, state)
                logger.success(f"[{worker_id}] task {task_id[:8]} COMPLETE")
            except Exception as e:
                # `run_pipeline` ya marcó FAILED en state. Solo loggeamos.
                logger.exception(f"[{worker_id}] task {task_id[:8]} FAILED: {e}")
                # No re-raise: el worker debe seguir consumiendo.
    finally:
        logger.info(f"[{worker_id}] draining: closing queue/state")
        try:
            await queue.close()
        except Exception as e:  # pragma: no cover
            logger.warning(f"[{worker_id}] queue.close: {e}")
        try:
            await state.close()
        except Exception as e:  # pragma: no cover
            logger.warning(f"[{worker_id}] state.close: {e}")
        logger.info(f"[{worker_id}] exited cleanly")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
