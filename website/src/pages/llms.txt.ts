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
  ["pipeline", "DAG completo en detalle: 18 reasoners + visual selector + editor"],
  ["cost_model", "Tabla de costos por modo (express/premium/brand-owned/long-form)"],
  ["troubleshooting", "Errores comunes y sus fixes"],
  ["api_reference", "14 endpoints REST documentados"],
  ["decisions", "ADRs 1-15 — por qué cada decisión arquitectónica"],
  ["migration_from_mpt", "Mapeo desde MoneyPrinterTurbo"],
  ["contributing", "Guía de contribución"],
];

export const GET: APIRoute = () => {
  const body = `# contenido

> Plataforma open source para generación de video con IA: reels cinematográficos de 25s, video largo de 5-60min (documentales, libros animados), brand-owned con LoRA propia. Fusiona cinco linajes: MoneyPrinterTurbo (industrial), reels-af (DAG cognitivo de 18 reasoners), corredor-content (editorial layer), ComfyUI (visual ownership multi-tenant) y HKUDS/ViMax (long-form). Python 3.11+. Apache 2.0. 413 tests verde.

Este proyecto resuelve un problema real: las herramientas de generación de video con IA actuales producen "AI slop" sin identidad de marca y máximo 25 segundos. \`contenido\` permite (1) entrenar tu LoRA con 30-50 fotos via wizard incluido, (2) usarla en 7 workflows ComfyUI distintos (Flux + ControlNet + IPAdapter + AnimateDiff + Inpaint + Upscale), (3) escalar de reels cortos a video largo de 60min reusando el mismo stack. Self-hosted (GPU local) o managed (ViewComfy/RunComfy).

Stack: Python 3.11 + FastAPI + Streamlit + AgentField + Pydantic 2 + ComfyUI + Higgsfield + Replicate + sentence-transformers + langchain-text-splitters + ffmpeg + libass.

## Documentación principal

${docs.map(([slug, desc]) => `- [${slug}](${SITE}/docs/${slug}): ${desc}`).join("\n")}

## Capas del sistema

- [Editorial layer](${SITE}/docs/editorial): Brand voice como código (Markdown versionado en git), facts.json para anti-alucinación de hunters, pillars rotables, gate humano plan→approve→produce-week, platform specs (TikTok/Reels/Shorts/Long/FB/LinkedIn), cost tracking USD por LLM call.
- [ComfyUI multi-tenant](${SITE}/docs/comfyui): 7 workflows JSON pre-armados, ComfyParameterMap para reusabilidad, REST + WebSocket client async, OOM auto-retry (POST /free), workflow versioning SHA256, brand-visual.json para multi-tenant (cada cliente con su LoRA), LoRA training wizard (Replicate cloud ~$2-3 o kohya local).
- [Long-form video](${SITE}/docs/long_form): NovelCompressor (chunks + parallel LLM compress), RAGStore híbrido numpy/FAISS, ScriptPlanner con intent routing narrative/motion/montage, SceneExtractor, StoryboardArtist con cinematic language, ReferenceImageSelector + BestImageSelector VLM. Inspirado en HKUDS/ViMax pero reemplaza LangChain wholesale por core.llm_router.

## Capabilities matrix (13 backends)

ComfyUI workflows (7):
- [flux_basic_9x16](${SITE}/docs/comfyui): Flux dev txt2img vertical sin LoRA, ~18s
- [flux_lora_brand](${SITE}/docs/comfyui): Flux + Brand LoRA (multi-tenant), ~22s
- [flux_controlnet_pose](${SITE}/docs/comfyui): Layout strict pose/depth/canny, ~28s
- [sdxl_ipadapter_style](${SITE}/docs/comfyui): Style transfer desde reference image, ~28s
- [animatediff_lora](${SITE}/docs/comfyui): Video t2v con Brand LoRA (16 frames), ~75s
- [inpaint_brand](${SITE}/docs/comfyui): Producto cambia, fondo persiste, ~30s
- [upscale_face_restore](${SITE}/docs/comfyui): Post-process 4x upscale + face restore, ~8s

Higgsfield (3): DoP turbo (~\$0.20/clip 5s), Soul (character consistency cross-beat), Effects (VFX overlay)
Stock (1): Pexels/Pixabay/Coverr fallback
Long-form (2): plan (book→script, ~\$1-4) + produce (shots→final, ~\$15-80)

## Costos

- Express + Edge + Pexels: ~\$0.001/reel
- Premium + ken-burns: ~\$0.08/reel
- Premium + Higgsfield DoP: ~\$1.10/reel
- Premium + ComfyUI brand LoRA self-host: ~\$0.04-0.15/reel
- Long-form 10min: ~\$16-24
- Long-form 60min: ~\$70-80
- LoRA training (one-time): \$2-3 cloud / gratis self-host

## Operativo para agentes (machine-readable)

- [OpenAPI 3.1 spec](${SITE}/openapi.json): Schema canónico generado por FastAPI desde modelos Pydantic — 13 paths, 14 ops. Para que un LLM agente construya requests válidos sin adivinar fields.
- [AI plugin manifest](${SITE}/.well-known/ai-plugin.json): Manifest \`schema_version: v1\` (OpenAI plugin spec). Apunta al openapi.json y describe \`description_for_model\` con capabilities.
- [A2A Agent Card](${SITE}/.well-known/agents.json): Agent Card v1 (a2a.dev). Skills declaradas: video.generate_from_topic, video.generate_from_url, video.generate_long_form, comfyui.train_lora, task.status, task.costs.
- [RSS feed](${SITE}/rss.xml): RSS 2.0 de los 13 docs — para crawlers AI que descubren via feed (Perplexity, ChatGPT, Claude).
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
