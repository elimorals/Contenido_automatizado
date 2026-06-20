/**
 * /llms.txt — discovery file para Large Language Models.
 *
 * Spec: https://llmstxt.org/ (Jeremy Howard / Answer.AI, Aug 2024)
 *
 * Formato:
 *   # Title (H1, required)
 *   > Blockquote summary
 *   Detail prose paragraphs
 *   ## Section (H2)
 *   - [Title](url): brief note
 *
 * Diferencia con sitemap.xml: sitemap es para crawlers tradicionales
 * (Google), llms.txt es para LLMs en runtime que necesitan entender
 * qué hay en el sitio y dónde está la documentación canónica.
 *
 * Acceso: https://contenido.vercel.app/llms.txt
 */

import type { APIRoute } from "astro";

const SITE = "https://contenido.vercel.app";
const REPO = "https://github.com/elimorals/Contenido_automatizado";

const docs = [
  ["getting_started", "Tutorial paso a paso desde git clone hasta primer reel (~15 min)"],
  ["api_keys", "Qué API keys necesitas (mínimo OPENROUTER + PEXELS), costos por provider"],
  ["configuration", "Referencia completa de config.toml + todas las env vars"],
  ["editorial", "Brand voice como código + facts.json anti-alucinación + gate humano plan→produce"],
  ["comfyui", "7 workflows ComfyUI + LoRA training wizard + multi-tenant + OOM auto-retry"],
  ["long_form", "Pipeline ViMax-inspired para video largo 5-60min con RAG + VLM consistency"],
  ["live_avatar", "Talking-head audio-driven con lip-sync (LiveAvatar Alibaba-Quark, ADR-016)"],
  ["pipeline", "DAG completo en detalle: 18 reasoners + visual selector + editor"],
  ["mcp", "Servidor MCP: usa el pipeline desde un agente (Claude/Cursor). Job-based + gate de costo (ADR-021)"],
  ["cost_model", "Tabla de costos por modo (express/premium/brand-owned/long-form)"],
  ["troubleshooting", "Errores comunes y sus fixes"],
  ["api_reference", "Endpoints REST documentados (incluye POST /reference para input-by-reference)"],
  ["decisions", "ADRs 1-24 — por qué cada decisión arquitectónica"],
  ["migration_from_mpt", "Mapeo desde MoneyPrinterTurbo"],
  ["contributing", "Guía de contribución"],
];

export const GET: APIRoute = () => {
  const body = `# contenido

> Plataforma open source para generación de video con IA: reels cinematográficos de 25s, video largo de 5-60min (documentales, libros animados, talking-head con lip-sync), brand-owned con LoRA propia. Fusiona cinco linajes: MoneyPrinterTurbo (industrial), reels-af (DAG cognitivo de 18 reasoners), corredor-content (editorial layer), ComfyUI (visual ownership multi-tenant) y HKUDS/ViMax (long-form). LiveAvatar (Alibaba-Quark, ECCV 2026) para avatares parlantes audio-driven (ADR-016). Operable por agentes vía servidor MCP (ADR-021). Python 3.11+. Apache 2.0. 562 tests verde.

Este proyecto resuelve un problema real: las herramientas de generación de video con IA actuales producen "AI slop" sin identidad de marca y máximo 25 segundos. \`contenido\` permite (1) entrenar tu LoRA con 30-50 fotos via wizard incluido, (2) usarla en 7 workflows ComfyUI distintos (Flux + ControlNet + IPAdapter + AnimateDiff + Inpaint + Upscale), (3) escalar de reels cortos a video largo de 60min reusando el mismo stack, (4) clonar el ritmo/estructura de un video de referencia (input-by-reference, ADR-017). Cuatro superficies: CLI, REST API, WebUI Streamlit y servidor MCP. Self-hosted (GPU local) o managed (ViewComfy/RunComfy).

Stack: Python 3.11 + FastAPI + Streamlit + AgentField + Pydantic 2 + MCP (FastMCP) + ComfyUI + Higgsfield + fal.ai + Replicate + sentence-transformers + langchain-text-splitters + ffmpeg + libass.

## Documentación principal

${docs.map(([slug, desc]) => `- [${slug}](${SITE}/docs/${slug}): ${desc}`).join("\n")}

## Capas del sistema

- [Editorial layer](${SITE}/docs/editorial): Brand voice como código (Markdown versionado en git), facts.json para anti-alucinación de hunters, pillars rotables, gate humano plan→approve→produce-week, platform specs (TikTok/Reels/Shorts/Long/FB/LinkedIn), cost tracking USD por LLM call.
- [ComfyUI multi-tenant](${SITE}/docs/comfyui): 7 workflows JSON pre-armados, ComfyParameterMap para reusabilidad, REST + WebSocket client async, OOM auto-retry (POST /free), workflow versioning SHA256, brand-visual.json para multi-tenant (cada cliente con su LoRA), LoRA training wizard (Replicate cloud ~$2-3 o kohya local).
- [Long-form video](${SITE}/docs/long_form): NovelCompressor (chunks + parallel LLM compress), RAGStore híbrido numpy/FAISS, ScriptPlanner con intent routing narrative/motion/montage, SceneExtractor, StoryboardArtist con cinematic language, ReferenceImageSelector + BestImageSelector VLM. Inspirado en HKUDS/ViMax pero reemplaza LangChain wholesale por core.llm_router.

## Capabilities matrix

ComfyUI workflows (7):
- [flux_basic_9x16](${SITE}/docs/comfyui): Flux dev txt2img vertical sin LoRA, ~18s
- [flux_lora_brand](${SITE}/docs/comfyui): Flux + Brand LoRA (multi-tenant), ~22s
- [flux_controlnet_pose](${SITE}/docs/comfyui): Layout strict pose/depth/canny, ~28s
- [sdxl_ipadapter_style](${SITE}/docs/comfyui): Style transfer desde reference image, ~28s
- [animatediff_lora](${SITE}/docs/comfyui): Video t2v con Brand LoRA (16 frames), ~75s
- [inpaint_brand](${SITE}/docs/comfyui): Producto cambia, fondo persiste, ~30s
- [upscale_face_restore](${SITE}/docs/comfyui): Post-process 4x upscale + face restore, ~8s

Higgsfield (3): DoP turbo (~\$0.20/clip 5s), Soul (character consistency cross-beat), Effects (VFX overlay)
fal.ai i2v (3, ADR-023): Kling / Runway / MiniMax — fallback de motion opt-in entre Veo y ken-burns (~\$0.25/clip)
LiveAvatar (2): remote_http (RunPod/Lambda, ~\$0.05/s) + local_cli (self-host single-GPU 80GB, ~\$0.005/s amortizado) — talking-head audio-driven con lip-sync, ADR-016
Stock de pago (3): Pexels / Pixabay / Coverr
Corpus libre (4, ADR-022): Archive.org · Wikimedia Commons · NASA (video keyless) · Unsplash (imágenes → ken-burns) — \$0/clip
B-roll re-ranking semántico (ADR-018): reordena candidatos de stock por relevancia (léxico por default, embeddings sentence-transformers opt-in)
Long-form (2): plan (book→script, ~\$1-4) + produce (shots→final, ~\$15-80)

## Input-by-reference (ADR-017)

Analiza un video de referencia (TikTok/Reel/YouTube) y extrae su pacing, hook, estructura y transcript en un brief que informa la composición del guion ("haz un reel con el ritmo de este video"). Disponible en las 4 superficies: CLI \`contenido reference <url>\`, POST /reference, WebUI, y tool MCP \`contenido_analyze_reference\`. El brief modula el estilo de hook, el WPM objetivo y el número de beats.

## Calidad observable

- Anti-slideshow guard (ADR-019): detecta reels que prometieron movimiento pero quedaron dominados por stills; expone slideshow_risk/static_ratio en quality_flags y badge en la WebUI.
- Checkpoint reanudable (ADR-024): persistencia por job de fases/beats completados para reanudar long-form sin repetir generaciones caras.

## Costos

- Express + Edge + Pexels: ~\$0.001/reel
- Premium + ken-burns: ~\$0.08/reel
- Premium + Higgsfield DoP: ~\$1.10/reel
- Premium + ComfyUI brand LoRA self-host: ~\$0.04-0.15/reel
- Long-form 10min: ~\$16-24
- Long-form 60min: ~\$70-80
- Talking-head 10min (remote_http RunPod H100): ~\$30
- Talking-head 10min (local_cli amortizado): ~\$3 (electricidad)
- LoRA training (one-time): \$2-3 cloud / gratis self-host

## Operativo para agentes (machine-readable)

- [MCP server](${SITE}/docs/mcp): Servidor Model Context Protocol (FastMCP, stdio) que expone el pipeline como tools a un agente (Claude Code/Desktop, Cursor): contenido_analyze_reference, contenido_start_reel (job-based, devuelve cost_note), contenido_get_task, contenido_list_tasks, contenido_list_voices. Gate de costo: sólo reels (el long-form sigue human-gated). ADR-021.
- [OpenAPI 3.1 spec](${SITE}/openapi.json): Schema canónico generado por FastAPI desde modelos Pydantic. Para que un LLM agente construya requests válidos sin adivinar fields.
- [AI plugin manifest](${SITE}/.well-known/ai-plugin.json): Manifest \`schema_version: v1\` (OpenAI plugin spec). Apunta al openapi.json y describe \`description_for_model\` con capabilities.
- [A2A Agent Card](${SITE}/.well-known/agents.json): Agent Card v1 (a2a.dev). Skills declaradas: video.generate_from_topic, video.generate_from_url, video.generate_long_form, comfyui.train_lora, task.status, task.costs.
- [RSS feed](${SITE}/rss.xml): RSS 2.0 de los 15 docs — para crawlers AI que descubren via feed (Perplexity, ChatGPT, Claude).
- [security.txt](${SITE}/.well-known/security.txt): RFC 9116 — política de coordinated disclosure 90 días.

## Optional

- [GitHub repository](${REPO}): Source code, issues, releases
- [Sitemap XML](${SITE}/sitemap-index.xml): Para crawlers tradicionales
- [README en repo](${REPO}/blob/main/README.md): Quick start completo
- [STATUS](${REPO}/blob/main/STATUS.md): Estado actual de implementación
- [ARCHITECTURE](${REPO}/blob/main/ARCHITECTURE.md): Decisiones técnicas detalladas
- [humans.txt](${SITE}/humans.txt): Créditos a los linajes fusionados
`;

  return new Response(body, {
    status: 200,
    headers: {
      "Content-Type": "text/plain; charset=utf-8",
      "Cache-Control": "public, max-age=3600",
    },
  });
};
