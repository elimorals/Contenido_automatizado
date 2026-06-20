"""Checkpoint reanudable por task (ADR-024).

Store JSON atómico que registra qué fases y qué beats de un job ya se completaron,
con sus artefactos, para que un re-run salte trabajo ya hecho. Es marginal para
reels de 25s (rápidos y baratos) pero valioso para long-form (45-90 min, $15-80):
si el proceso muere en el shot 40/120, reanudar evita repetir 39 generaciones caras.

Diseño: agnóstico al pipeline. El caller consulta `is_phase_done` / `pending_beats`
antes de cada fase y registra con `mark_phase` / `mark_beat` + `save`. No hay hooks
mágicos — el pipeline decide qué checkpointear (igual que el patrón de OpenMontage,
reimplementado bajo Apache-2.0).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger


@dataclass
class Checkpoint:
    """Estado reanudable de un job. Mutable; se persiste con `CheckpointStore.save`."""

    task_id: str
    phases: dict[str, dict[str, Any]] = field(default_factory=dict)
    beats: dict[str, dict[str, Any]] = field(default_factory=dict)

    # ── fases ──────────────────────────────────────────────────────────────
    def mark_phase(self, name: str, data: dict[str, Any] | None = None) -> None:
        self.phases[name] = dict(data or {})

    def is_phase_done(self, name: str) -> bool:
        return name in self.phases

    def phase_data(self, name: str) -> dict[str, Any]:
        return self.phases.get(name, {})

    # ── beats ──────────────────────────────────────────────────────────────
    def mark_beat(self, idx: int, data: dict[str, Any] | None = None) -> None:
        self.beats[str(idx)] = dict(data or {})

    def beat_done(self, idx: int) -> bool:
        return str(idx) in self.beats

    def beat_data(self, idx: int) -> dict[str, Any]:
        return self.beats.get(str(idx), {})

    def pending_beats(self, total: int) -> list[int]:
        """Índices [0, total) que aún NO están completados, en orden."""
        return [i for i in range(total) if not self.beat_done(i)]

    # ── serialización ──────────────────────────────────────────────────────
    def to_dict(self) -> dict[str, Any]:
        return {"task_id": self.task_id, "phases": self.phases, "beats": self.beats}

    @classmethod
    def from_dict(cls, data: dict[str, Any], task_id: str) -> Checkpoint:
        return cls(
            task_id=data.get("task_id", task_id),
            phases=dict(data.get("phases", {})),
            beats=dict(data.get("beats", {})),
        )


class CheckpointStore:
    """Lee/escribe checkpoints como `<dir>/<task_id>.checkpoint.json` (atómico)."""

    def __init__(self, base_dir: Path | str) -> None:
        self.base_dir = Path(base_dir)

    def _path(self, task_id: str) -> Path:
        return self.base_dir / f"{task_id}.checkpoint.json"

    def exists(self, task_id: str) -> bool:
        return self._path(task_id).exists()

    def load(self, task_id: str) -> Checkpoint:
        """Carga el checkpoint; devuelve uno vacío si no existe o está corrupto."""
        path = self._path(task_id)
        if not path.exists():
            return Checkpoint(task_id=task_id)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"[checkpoint] {task_id} corrupto/inaccesible ({e}); empezando limpio")
            return Checkpoint(task_id=task_id)
        return Checkpoint.from_dict(data, task_id)

    def save(self, checkpoint: Checkpoint) -> None:
        """Escribe atómicamente (tmp + replace) para no dejar JSON a medias."""
        self.base_dir.mkdir(parents=True, exist_ok=True)
        path = self._path(checkpoint.task_id)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(checkpoint.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)

    def clear(self, task_id: str) -> None:
        """Elimina el checkpoint (p.ej. tras completar el job con éxito)."""
        self._path(task_id).unlink(missing_ok=True)


def get_checkpoint_store(base_dir: Path | str | None = None) -> CheckpointStore:
    """Store apuntando a `base_dir`, o a `<long_form.working_dir>/checkpoints` por default."""
    if base_dir is None:
        from shared.config import load_config

        base_dir = Path(load_config().long_form.working_dir) / "checkpoints"
    return CheckpointStore(base_dir)


__all__ = ["Checkpoint", "CheckpointStore", "get_checkpoint_store"]
