"""LoRA training wizard: convierte "tengo 30-50 fotos" en "tengo un .safetensors".

Soporta dos backends:
- `replicate` (cloud, $5-15/train, ~25 min): wrapper sobre Replicate ai-toolkit
  para Flux LoRA training. No requiere GPU local.
- `kohya` (local, $0, requiere GPU 16GB+, ~4-8 horas): instrucciones generadas
  para correr kohya_ss localmente. NO ejecutamos el training nosotros —
  damos al usuario un comando listo para copy/paste.

Validation:
- 5-50 imágenes (sweet spot 20-30, per kohya recommendation)
- Resolución ≥ 1024×1024
- JPG o PNG
- No watermarks / texto en frame
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from loguru import logger
from PIL import Image


class TrainingValidationError(ValueError):
    """Dataset no cumple requisitos mínimos para entrenamiento."""


@dataclass
class TrainingDataset:
    """Dataset validado listo para subir al trainer."""

    image_paths: list[Path] = field(default_factory=list)
    caption_paths: list[Path] = field(default_factory=list)
    name: str = "brand_v1"
    trigger_word: str = ""  # ej: "rt0brand" — único en captions


# Requisitos del photo-guide (similares al Soul de Higgsfield)
TRAINING_MIN_IMAGES = 5
TRAINING_MAX_IMAGES = 50
TRAINING_SWEET_SPOT = (20, 30)
MIN_RESOLUTION = 1024
ALLOWED_EXTS = (".jpg", ".jpeg", ".png", ".webp")


def validate_training_dataset(
    image_dir: Path,
    *,
    strict: bool = False,
) -> tuple[list[Path], list[str]]:
    """Valida dir de imágenes contra photo-guide.

    Returns: (image_paths_validas, warnings).
    Si strict=True, lanza TrainingValidationError ante cualquier violación.
    """
    if not image_dir.is_dir():
        raise TrainingValidationError(f"image_dir no existe: {image_dir}")

    candidates = [
        p for p in sorted(image_dir.iterdir())
        if p.suffix.lower() in ALLOWED_EXTS
    ]
    warnings: list[str] = []

    if len(candidates) < TRAINING_MIN_IMAGES:
        msg = f"{len(candidates)} imágenes; mínimo {TRAINING_MIN_IMAGES}"
        if strict:
            raise TrainingValidationError(msg)
        warnings.append(msg)
    if len(candidates) > TRAINING_MAX_IMAGES:
        warnings.append(
            f"{len(candidates)} imágenes; máximo {TRAINING_MAX_IMAGES} "
            "(se tomarán las primeras)"
        )
        candidates = candidates[:TRAINING_MAX_IMAGES]

    lo, hi = TRAINING_SWEET_SPOT
    if not (lo <= len(candidates) <= hi):
        warnings.append(
            f"{len(candidates)} fuera del sweet spot {lo}-{hi} (calidad subóptima)"
        )

    valid: list[Path] = []
    for p in candidates:
        try:
            with Image.open(p) as img:
                w, h = img.size
            if min(w, h) < MIN_RESOLUTION:
                msg = f"{p.name}: {w}×{h} < {MIN_RESOLUTION} mínimo"
                if strict:
                    raise TrainingValidationError(msg)
                warnings.append(msg)
                continue
            valid.append(p)
        except Exception as e:  # noqa: BLE001
            warnings.append(f"{p.name}: no se pudo leer ({e})")

    return valid, warnings


# =============================================================================
# Backend: Replicate (cloud)
# =============================================================================


@dataclass
class ReplicateTrainingPlan:
    """Plan de training en Replicate ai-toolkit — listo para ejecutar."""

    trainer_model: str = "ostris/flux-dev-lora-trainer"
    trainer_version: str = ""  # Resuelto vía API si vacío
    api_token_env: str = "REPLICATE_API_TOKEN"

    # Parámetros del trainer
    steps: int = 1000
    learning_rate: float = 4e-4
    batch_size: int = 1
    resolution: int = 1024
    autocaption: bool = True
    trigger_word: str = ""
    estimated_cost_usd: float = 0.0
    estimated_minutes: int = 25


def _plan_replicate_training(
    name: str,
    image_count: int,
    trigger_word: str,
    steps: int = 1000,
) -> ReplicateTrainingPlan:
    """Genera plan de costo + parámetros para Flux LoRA en Replicate."""
    # Replicate ai-toolkit ~$0.0014/sec H100, ~1500 sec/run = $2-3 típico
    estimated_sec = steps * 1.5  # estimate
    cost_per_sec = 0.0014  # H100 80GB
    estimated_cost = round(estimated_sec * cost_per_sec, 2)
    return ReplicateTrainingPlan(
        steps=steps,
        trigger_word=trigger_word or f"{name}_brand",
        estimated_cost_usd=estimated_cost,
        estimated_minutes=int(estimated_sec / 60),
    )


def replicate_training_command(
    name: str,
    image_dir: Path,
    *,
    trigger_word: str = "",
    steps: int = 1000,
) -> dict:
    """Genera el comando concreto para correr el training en Replicate.

    Devuelve dict con:
        plan: ReplicateTrainingPlan
        cli_command: comando bash listo
        api_call_python: snippet Python si prefieres SDK
        instructions: lista de pasos manuales si nunca lo hiciste

    NO ejecuta nada. Replicate cobra cuando el usuario aprieta enter.
    """
    plan = _plan_replicate_training(
        name=name, image_count=0, trigger_word=trigger_word, steps=steps,
    )
    tar_path = image_dir.parent / f"{name}_dataset.tar.gz"

    cli_command = (
        f"# 1) Empaqueta el dataset\n"
        f"tar -czf {tar_path} -C {image_dir.parent} {image_dir.name}\n\n"
        f"# 2) Crea un model destino en Replicate (una vez)\n"
        f"replicate model create your-username/{name}-lora \\\n"
        f"    --hardware=cpu --visibility=private\n\n"
        f"# 3) Lanza el training (~{plan.estimated_minutes} min, ~${plan.estimated_cost_usd})\n"
        f"replicate train {plan.trainer_model}:latest \\\n"
        f"    --destination=your-username/{name}-lora \\\n"
        f"    -i input_images=@{tar_path} \\\n"
        f"    -i steps={plan.steps} \\\n"
        f"    -i lora_rank=16 \\\n"
        f"    -i trigger_word={plan.trigger_word} \\\n"
        f"    -i autocaption={'true' if plan.autocaption else 'false'} \\\n"
        f"    -i learning_rate={plan.learning_rate}\n"
    )

    api_call_python = (
        f"import replicate, os\n"
        f"os.environ['REPLICATE_API_TOKEN'] = 'tu_token'\n\n"
        f"training = replicate.trainings.create(\n"
        f"    destination='your-username/{name}-lora',\n"
        f"    version='{plan.trainer_model}:latest',\n"
        f"    input={{\n"
        f"        'input_images': open('{tar_path}', 'rb'),\n"
        f"        'steps': {plan.steps},\n"
        f"        'lora_rank': 16,\n"
        f"        'trigger_word': '{plan.trigger_word}',\n"
        f"        'autocaption': True,\n"
        f"        'learning_rate': {plan.learning_rate},\n"
        f"    }},\n"
        f")\n"
        f"print(training.id, training.status)\n"
        f"# Cuando termine: training.output['version'] tiene el LoRA URL\n"
    )

    instructions = [
        "1. Crea cuenta en https://replicate.com y agrega tarjeta",
        "2. Genera API token en https://replicate.com/account/api-tokens",
        "3. Export REPLICATE_API_TOKEN=tu_token",
        "4. pip install replicate",
        f"5. Crea destino: replicate model create your-username/{name}-lora --visibility=private",
        "6. Corre el comando bash de abajo (o el snippet Python)",
        "7. Espera ~25 min. Cuando termine, descarga el .safetensors del output URL.",
        f"8. Usa: contenido comfy lora download --url <url> --filename {name}_v1.safetensors",
        f"9. Edita editorial/brand-visual.json setea \"lora_name\": \"{name}_v1.safetensors\"",
    ]

    return {
        "plan": plan,
        "cli_command": cli_command,
        "api_call_python": api_call_python,
        "instructions": instructions,
    }


# =============================================================================
# Backend: kohya (local) — solo instrucciones, NO ejecución
# =============================================================================


def kohya_training_command(
    name: str,
    image_dir: Path,
    *,
    trigger_word: str = "",
    base_model: Literal["flux", "sdxl"] = "flux",
    steps: int = 1500,
) -> dict:
    """Genera comando kohya_ss listo para correr localmente.

    No ejecuta — el usuario lo corre manualmente porque kohya tiene
    GUI propia y los paths son específicos del setup local.
    """
    trigger = trigger_word or f"{name}_brand"

    if base_model == "flux":
        script = "flux_train_network.py"
        extra = (
            "--pretrained_model_name_or_path=/path/to/flux1-dev.safetensors "
            "--clip_l=/path/to/clip_l.safetensors --t5xxl=/path/to/t5xxl_fp16.safetensors "
            "--ae=/path/to/ae.safetensors"
        )
    else:
        script = "sdxl_train_network.py"
        extra = "--pretrained_model_name_or_path=/path/to/sd_xl_base_1.0.safetensors"

    cli_command = (
        f"# Pre-requisito: clonar kohya_ss y crear env\n"
        f"# git clone https://github.com/kohya-ss/sd-scripts && cd sd-scripts\n"
        f"# python -m venv venv && source venv/bin/activate\n"
        f"# pip install -r requirements.txt && pip install torch xformers\n\n"
        f"accelerate launch --num_cpu_threads_per_process 8 {script} \\\n"
        f"    {extra} \\\n"
        f"    --train_data_dir={image_dir} \\\n"
        f"    --output_dir=./output_lora \\\n"
        f"    --output_name={name}_v1 \\\n"
        f"    --resolution=1024,1024 \\\n"
        f"    --network_module=networks.lora_flux \\\n"
        f"    --network_dim=16 \\\n"
        f"    --network_alpha=16 \\\n"
        f"    --learning_rate=4e-4 \\\n"
        f"    --max_train_steps={steps} \\\n"
        f"    --train_batch_size=1 \\\n"
        f"    --save_every_n_steps=500 \\\n"
        f"    --mixed_precision=bf16 \\\n"
        f"    --save_precision=bf16 \\\n"
        f"    --gradient_checkpointing\n"
    )

    instructions = [
        "1. Asegúrate de tener GPU NVIDIA 16GB+ (recomendado 24GB para Flux)",
        "2. Crea estructura: image_dir/10_trigger/*.jpg (10_ = repetitions × concept)",
        "3. Captioning: usa BLIP o GPT-4V para generar .txt al lado de cada imagen",
        f"4. Cada caption debe incluir '{trigger}' como token único de tu marca",
        "5. Corre el comando bash de abajo (4-8 horas, depende GPU)",
        f"6. Cuando termine: cp output_lora/{name}_v1.safetensors ~/comfy/models/loras/",
        f"7. Edita editorial/brand-visual.json: \"lora_name\": \"{name}_v1.safetensors\"",
    ]

    return {
        "trigger_word": trigger,
        "estimated_hours": 6 if base_model == "flux" else 4,
        "cli_command": cli_command,
        "instructions": instructions,
    }


# =============================================================================
# Helper público (dispatcher)
# =============================================================================


def plan_lora_training(
    name: str,
    image_dir: Path,
    *,
    backend: Literal["replicate", "kohya"] = "replicate",
    trigger_word: str = "",
    base_model: Literal["flux", "sdxl"] = "flux",
    steps: int | None = None,
    strict: bool = False,
) -> dict:
    """Plan de training: valida dataset + genera plan para el backend elegido.

    Returns dict con:
        valid_images: list[Path] (imágenes que pasaron validación)
        warnings: list[str]
        plan: dict (dependiente del backend)
        backend: str
    """
    valid, warnings = validate_training_dataset(image_dir, strict=strict)
    if not valid:
        raise TrainingValidationError(
            f"0 imágenes válidas en {image_dir} — no se puede entrenar"
        )

    if backend == "replicate":
        plan = replicate_training_command(
            name=name, image_dir=image_dir, trigger_word=trigger_word,
            steps=steps or 1000,
        )
    elif backend == "kohya":
        plan = kohya_training_command(
            name=name, image_dir=image_dir, trigger_word=trigger_word,
            base_model=base_model, steps=steps or 1500,
        )
    else:
        raise ValueError(f"backend desconocido: {backend}")

    return {
        "valid_images": valid,
        "warnings": warnings,
        "plan": plan,
        "backend": backend,
    }
