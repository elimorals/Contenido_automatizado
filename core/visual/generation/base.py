"""Interfaz común para generadores visuales IA (Gemini Image, Veo, ken-burns).

Cada generador implementa `VisualGenerator` y produce un `BeatArtifact`
con `first_frame_path` y/o `video_path` populados. Es responsabilidad
del orquestador combinarlos (frame → motion) y manejar fallbacks.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from shared.schemas import Beat, BeatArtifact, BeatVisual


class VisualGenerationError(RuntimeError):
    """Error genérico de un generador visual.

    Los generadores deben lanzarla en fallo blando (API no disponible,
    respuesta vacía). El orquestador la captura y aplica fallback.
    """


class VisualGenerator(ABC):
    """Contrato común para generadores visuales (imagen o video) por beat."""

    name: str = ""

    @abstractmethod
    async def generate(
        self,
        beat: Beat,
        visual: BeatVisual,
        content_mode: str,
        out_dir: Path,
    ) -> BeatArtifact:
        """Genera artefactos visuales para un beat.

        Args:
            beat: Beat con `idx`, `role`, `target_duration_s`, `veo_duration`.
            visual: Visual instructions (image_prompt, motion_hint, anchor).
            content_mode: 'scientific' | 'general' — afecta style block.
            out_dir: Directorio destino. Generadores crean subpaths debajo.

        Returns:
            BeatArtifact con `first_frame_path` y/o `video_path` poblados.
            NUNCA debe devolver un artifact totalmente vacío — si todo
            falla, devolver placeholder.
        """
