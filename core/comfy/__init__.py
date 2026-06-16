"""Wrapper async sobre `comfy-cli` (instalación, launch, gestión de nodes/modelos).

Diferencia clara:
- `core.visual.generation.comfy_client` — REST + WS al server running.
- `core.comfy` (este paquete) — orquestar el server vía subprocess `comfy ...`.

Útil para:
- `contenido comfy install` (descarga ComfyUI)
- `contenido comfy launch --background` (arranca el server)
- `contenido comfy status` (alive check)
- `contenido comfy lora list / download <url>`
- `contenido comfy node install <name>`
- `contenido comfy workflow list`
"""
from __future__ import annotations

from core.comfy.training import (
    TrainingValidationError,
    kohya_training_command,
    plan_lora_training,
    replicate_training_command,
    validate_training_dataset,
)
from core.comfy.wrapper import (
    ComfyCLIError,
    ComfyCLINotInstalled,
    check_binary,
    cli_install,
    cli_launch,
    cli_status,
    download_lora,
    download_model,
    install_custom_node,
    list_loras,
    list_models,
    list_workflows,
)

__all__ = [
    "ComfyCLIError",
    "ComfyCLINotInstalled",
    "TrainingValidationError",
    "check_binary",
    "cli_install",
    "cli_launch",
    "cli_status",
    "download_lora",
    "download_model",
    "install_custom_node",
    "kohya_training_command",
    "list_loras",
    "list_models",
    "list_workflows",
    "plan_lora_training",
    "replicate_training_command",
    "validate_training_dataset",
]
