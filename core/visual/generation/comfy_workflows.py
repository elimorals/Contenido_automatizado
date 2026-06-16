"""Loader + parameterizer de workflows ComfyUI.

Workflows están en `workflows/*.json` en formato API (no GUI). Cada workflow
es un dict `{node_id: {class_type, inputs, ...}}`.

Para hacerlo multi-tenant + reusable, declaramos `ComfyWorkflowSpec` con
un `ComfyParameterMap` que dice: "el prompt va en node 6 input 'text', el
seed va en node 3 input 'seed'". Al runtime sustituimos sin modificar el
JSON del repo.

Pattern (ViewComfy): `{node_id}-inputs-{param_name}` → valor.
Ej: `"6-inputs-text": "a cat in a hat"` reemplaza `workflow["6"]["inputs"]["text"]`.
"""
from __future__ import annotations

import copy
import json
import random
from pathlib import Path
from typing import Any

from loguru import logger
from pydantic import BaseModel, Field

from shared.config import load_config
from shared.schemas import (
    BrandVisualConfig,
    ComfyOutputType,
    ComfyParameterMap,
    ComfyWorkflowKind,
    ComfyWorkflowSpec,
)


class WorkflowParameterError(ValueError):
    """Parámetro requerido por el spec no encontrado en el JSON."""


# =============================================================================
# Registry: descubrir specs desde workflows/
# =============================================================================


class WorkflowRegistryError(RuntimeError):
    """No se pudo cargar el registry de workflows."""


def _index_file_path() -> Path:
    """`workflows/index.json` con la lista de specs registrados."""
    wf_dir = Path(load_config().visual.comfyui.workflows_dir)
    return wf_dir / "index.json"


def load_registry() -> dict[str, ComfyWorkflowSpec]:
    """Carga `workflows/index.json` → {workflow_id: ComfyWorkflowSpec}."""
    idx = _index_file_path()
    if not idx.exists():
        logger.debug(f"[comfy.workflows] no index en {idx} — registry vacío")
        return {}
    try:
        raw = json.loads(idx.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise WorkflowRegistryError(f"index.json inválido: {e}") from e

    specs: dict[str, ComfyWorkflowSpec] = {}
    items = raw.get("workflows", [])
    for item in items:
        try:
            spec = ComfyWorkflowSpec(**item)
            specs[spec.id] = spec
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[comfy.workflows] spec inválido: {e}")
    return specs


def get_workflow_spec(workflow_id: str) -> ComfyWorkflowSpec:
    """Lookup en el registry. Raises KeyError si no existe."""
    registry = load_registry()
    if workflow_id not in registry:
        raise KeyError(
            f"workflow '{workflow_id}' no encontrado en registry. "
            f"Disponibles: {list(registry.keys())}"
        )
    return registry[workflow_id]


def load_workflow_json(spec: ComfyWorkflowSpec) -> dict[str, Any]:
    """Carga el JSON template del workflow (formato API)."""
    cfg = load_config().visual.comfyui
    raw_path = Path(spec.json_path)
    if not raw_path.is_absolute():
        raw_path = Path(cfg.workflows_dir) / spec.json_path
    if not raw_path.exists():
        raise FileNotFoundError(f"workflow JSON no existe: {raw_path}")
    try:
        return json.loads(raw_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise WorkflowParameterError(
            f"workflow JSON {raw_path} inválido: {e}"
        ) from e


# =============================================================================
# Parameterizer
# =============================================================================


def _apply_path(workflow: dict[str, Any], dotted: str, value: Any) -> None:
    """Aplica un override 'node-inputs-key' → workflow[node]['inputs'][key] = value.

    Pattern oficial de ViewComfy: `{node_id}-inputs-{param_name}`.
    """
    parts = dotted.split("-inputs-", 1)
    if len(parts) != 2:
        raise WorkflowParameterError(
            f"clave inválida '{dotted}' (esperado '{{node_id}}-inputs-{{param}}')"
        )
    node_id, param = parts[0], parts[1]
    if node_id not in workflow:
        raise WorkflowParameterError(
            f"node_id '{node_id}' no existe en workflow"
        )
    node = workflow[node_id]
    if "inputs" not in node or not isinstance(node["inputs"], dict):
        raise WorkflowParameterError(
            f"node {node_id} no tiene 'inputs' dict"
        )
    node["inputs"][param] = value


class WorkflowParams(BaseModel):
    """Valores semánticos a sustituir en un workflow.

    `ComfyParameterMap` (del spec) dice DÓNDE ponerlos.
    `WorkflowParams` (este) dice CUÁLES son los valores.
    """

    prompt: str | None = None
    negative_prompt: str | None = None
    seed: int | None = None
    width: int | None = None
    height: int | None = None
    steps: int | None = None
    cfg: float | None = None
    checkpoint: str | None = None
    lora_name: str | None = None
    lora_strength: float | None = None
    reference_image: str | None = None
    controlnet_image: str | None = None
    frames: int | None = None
    fps: int | None = None

    # Escape hatch para overrides arbitrarios
    custom: dict[str, Any] = Field(default_factory=dict)


def randomize_seed() -> int:
    """Seed pseudo-aleatorio en rango ComfyUI."""
    return random.randint(0, 2**32 - 1)


def parameterize_workflow(
    spec: ComfyWorkflowSpec,
    params: WorkflowParams,
    *,
    workflow_template: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Devuelve un workflow JSON nuevo con los parámetros sustituidos.

    No muta el template. Si un campo de `params` es None y existe mapping
    en `spec.parameters`, se ignora (mantiene el default del JSON).

    Args:
        spec: ComfyWorkflowSpec del registry.
        params: WorkflowParams con valores semánticos.
        workflow_template: dict del JSON. Si None, lo carga desde disk.

    Returns:
        Workflow dict listo para POST /prompt.

    Raises:
        WorkflowParameterError: si un mapping del spec apunta a un node_id
        inexistente o si el JSON tiene formato inválido.
    """
    wf = copy.deepcopy(workflow_template or load_workflow_json(spec))
    pmap = spec.parameters

    # Mapping campo semántico → coercion type (asegura tipos correctos en JSON)
    mappings = [
        (pmap.prompt, params.prompt, str),
        (pmap.negative_prompt, params.negative_prompt, str),
        (pmap.seed, params.seed, int),
        (pmap.width, params.width, int),
        (pmap.height, params.height, int),
        (pmap.steps, params.steps, int),
        (pmap.cfg, params.cfg, float),
        (pmap.checkpoint, params.checkpoint, str),
        (pmap.lora_name, params.lora_name, str),
        (pmap.lora_strength, params.lora_strength, float),
        (pmap.reference_image, params.reference_image, str),
        (pmap.controlnet_image, params.controlnet_image, str),
        (pmap.frames, params.frames, int),
        (pmap.fps, params.fps, int),
    ]
    for node_key, value, coerce in mappings:
        if node_key and value is not None:
            _apply_path(wf, node_key, coerce(value))

    # Customs del spec (overrides hardcoded por workflow)
    for key, value in pmap.custom.items():
        _apply_path(wf, key, value)

    # Customs del runtime (mayor prioridad — override por request)
    for key, value in params.custom.items():
        _apply_path(wf, key, value)

    return wf


# =============================================================================
# Brand → Params builder
# =============================================================================


def params_from_brand(
    brand: BrandVisualConfig,
    *,
    image_prompt: str,
    seed: int | None = None,
    width: int | None = None,
    height: int | None = None,
    reference_image: str | None = None,
) -> WorkflowParams:
    """Construye WorkflowParams desde un BrandVisualConfig + image prompt.

    Aplica:
    - Style suffix de marca al prompt
    - Negative prompt baseline
    - LoRA name + strength
    - Default width/height
    - Reference image si IPAdapter workflow
    """
    full_prompt = (
        f"{image_prompt.strip()}, {brand.style_suffix}"
        if brand.style_suffix
        else image_prompt
    )
    return WorkflowParams(
        prompt=full_prompt,
        negative_prompt=brand.negative_prompt,
        seed=seed if seed is not None else randomize_seed(),
        width=width or brand.default_width,
        height=height or brand.default_height,
        steps=brand.default_steps,
        cfg=brand.default_cfg,
        lora_name=brand.lora_name,
        lora_strength=brand.lora_strength,
        reference_image=reference_image or (
            brand.reference_images[0] if brand.reference_images else None
        ),
    )


# =============================================================================
# Validation
# =============================================================================


def validate_workflow_against_server(
    workflow: dict[str, Any],
    available_nodes: dict[str, Any],
) -> list[str]:
    """Best-effort: verifica que cada class_type del workflow existe en el server.

    `available_nodes` viene de `ComfyClient.object_info()`. Devuelve lista de
    warnings (class_types ausentes); vacía si todo OK.
    """
    warnings: list[str] = []
    for node_id, node in workflow.items():
        if not isinstance(node, dict):
            continue
        ct = node.get("class_type")
        if not ct:
            warnings.append(f"node {node_id} sin class_type")
            continue
        if ct not in available_nodes:
            warnings.append(
                f"node {node_id}: class_type '{ct}' no disponible en server "
                "(falta custom node?)"
            )
    return warnings
