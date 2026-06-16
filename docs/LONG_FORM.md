# Long-form Video (5-60 min)

Módulo `core/long_form/` para video largo narrativo: documentales, libros animados, YouTube long-form, audiolibros visuales. Inspirado en [ViMax (HKUDS)](https://github.com/HKUDS/ViMax) — MIT License.

## Filosofía

El pipeline base (`contenido topic/article/subject`) está optimizado para reels cortos (25s). Para video de 5-60 minutos necesitas:

- **Coherencia narrativa cross-scene**: una novela completa → 3 actos → 6-12 escenas → 30-100 shots
- **Consistencia visual cross-shot**: el protagonista debe verse igual en el shot 1 y en el shot 80
- **Compresión inteligente**: un libro de 150,000 palabras no cabe en el contexto del LLM tal cual
- **Selección de referencias**: cada nuevo shot necesita 1-8 frames previos como reference (IPAdapter)
- **Validación VLM**: generar N candidatos por shot y elegir el más consistente

`core/long_form/` cubre los 5 puntos sin duplicar lo que ya tienes (LLM router, TTS, ComfyUI, editor).

## Arquitectura

```
┌──────────────────────────────────────────────────────────────────┐
│  INPUT                                                            │
│  - idea (1 paragraph) → 10 min                                    │
│  - article URL → 15 min                                           │
│  - script (1-50k words) → 30 min                                  │
│  - novel (50-500k words) → 60 min                                 │
│  - podcast transcript → variable                                  │
└────────────────────────────┬─────────────────────────────────────┘
                             │
                             ▼
┌──────────────────────────────────────────────────────────────────┐
│  PHASE 1: PLAN (cheap, ~$1-3, 1-3 min)                            │
│                                                                   │
│  novel? ──► NovelCompressor (chunks + parallel compress)          │
│              └─► RAGStore (indexed for downstream queries)        │
│                                                                   │
│  source_text ──► detect_intent ──► narrative | motion | montage  │
│                                                                   │
│  ScriptPlanner ──► NarrativeArc (3 acts + themes + logline)      │
│                                                                   │
│  SceneExtractor ──► N scenes (location + characters + summary)    │
│                                                                   │
│  StoryboardArtist ──► M shots per scene (cinematic language)     │
│                                                                   │
│  Output: LongFormScript serialized to disk (gate humano)          │
└────────────────────────────┬─────────────────────────────────────┘
                             │
                             ▼ HUMAN GATE — review script, edit if needed
                             │
                             ▼
┌──────────────────────────────────────────────────────────────────┐
│  PHASE 2: PRODUCE (expensive, ~$5-30, 20-60 min)                  │
│                                                                   │
│  For each character without portrait:                             │
│    └─► ComfyUI flux_lora_brand → portrait.jpg                     │
│                                                                   │
│  For each scene → for each shot:                                  │
│    ReferenceImageSelector (VLM) ──► subset of N previous frames   │
│    Generate N candidates in parallel (ComfyUI + IPAdapter)        │
│    BestImageSelector (VLM) ──► best candidate                    │
│    └─► Higgsfield DoP / Veo i2v ──► motion clip                  │
│    └─► TTS narration (sample-accurate)                            │
│    Update ConsistencyAnchor                                       │
│                                                                   │
│  ffmpeg stitch all clips + audio + subs ──► final.mp4             │
└──────────────────────────────────────────────────────────────────┘
```

## Setup

```bash
# 1. Habilitar el módulo
echo "LONG_FORM_ENABLED=true" >> .env

# 2. (Opcional) FAISS para corpus >5k chunks
uv sync --extra longform-scale

# 3. Verificar
uv run python -c "from core.long_form import Director; print('OK')"
```

### Config (config.toml o env)

```toml
[long_form]
enabled = false                                    # LONG_FORM_ENABLED
working_dir = "./storage/long_form"
chunk_size_chars = 8000
chunk_overlap_chars = 800
embedding_model_name = "BAAI/bge-small-en-v1.5"   # LONG_FORM_EMBED_MODEL
embedding_device = "cpu"                          # "cuda" si GPU
faiss_enabled = false                             # opt-in via --extra longform-scale
top_k_retrieval = 5
chat_model_provider = "openrouter"
vlm_model_provider = "openrouter"
vlm_model_name = "google/gemini-2.5-flash"
candidates_per_shot = 3
max_reference_anchors = 8
default_target_minutes = 10.0
parallel_shot_concurrency = 4
```

### Env vars

| Variable | Default | Descripción |
|---|---|---|
| `LONG_FORM_ENABLED` | `false` | Habilita el módulo |
| `LONG_FORM_WORKING_DIR` | `./storage/long_form` | Dir base para jobs |
| `LONG_FORM_EMBED_MODEL` | `BAAI/bge-small-en-v1.5` | Embedding model |
| `LONG_FORM_EMBED_DEVICE` | `cpu` | `cpu` o `cuda` |
| `LONG_FORM_FAISS_ENABLED` | `false` | Opt-in al backend FAISS |
| `LONG_FORM_VLM_MODEL` | `google/gemini-2.5-flash` | Modelo VLM para consistency checks |

## Uso

### CLI

```bash
# 1. Plan (cheap — solo LLM, ~$1-3)
uv run python -m apps.cli.main book plan ./mi_idea.txt \
    --target-minutes 10 \
    --source-kind idea \
    --intent auto

# 2. Inspeccionar
uv run python -m apps.cli.main book show <job_id>

# 3. (TODO: requiere GPU) Producir
uv run python -m apps.cli.main book produce <job_id>
```

### Python API

```python
from core.long_form import plan_long_form, produce_long_form, Director

# Fase 1: plan
text = Path("mi_novela.txt").read_text()
script, job = await plan_long_form(
    source_text=text,
    source_kind="novel",
    target_minutes=30.0,
)
print(f"Generado: {script.arc.title}")
print(f"Scenes: {len(script.scenes)} × shots ≈ {script.total_shots / len(script.scenes):.0f}")

# Inspección humana
for scene in script.scenes:
    print(f"Scene {scene.idx}: {scene.title}")
    for shot in scene.shots:
        print(f"  Shot {shot.idx}: {shot.visual_description[:80]}")

# Recuperar después
job, script = Director.load_job(job.job_id)

# Fase 2: producir (TODO: requiere GPU + ComfyUI)
job = await produce_long_form(job, script)
```

## Componentes

### NovelCompressor (`compressor.py`)

Para libros largos que no caben en el contexto del LLM. Divide en chunks de 8000 chars con overlap 800, los comprime en paralelo (semáforo 5) preservando plot + dialogue + character development, y los concatena.

```python
from core.long_form import NovelCompressor
c = NovelCompressor()
chunks = c.split(book_text)
results = await c.compress_all(chunks)
compressed = c.aggregate_compressed(results)
```

Prompts portados de `ViMax/agents/novel_compressor.py` (MIT).

### RAGStore (`rag.py`)

Híbrido: numpy default (≤5k chunks), FAISS opt-in (>5k chunks).

Backend numpy:
- Cosine similarity vía dot product (vectors normalizados de bge)
- `np.argpartition` para top-k O(N), no O(N log N)
- Persistencia `.npz`

Backend FAISS:
- Index `HNSWFlat(M=32, efConstruction=200)` — escala a millones
- Persistencia `faiss.write_index`

Embeddings: sentence-transformers local (`BAAI/bge-small-en-v1.5`, ~80MB).
Multilingüe: `BAAI/bge-m3` (~2GB) si tu corpus tiene varios idiomas.

```python
from core.long_form import RAGStore, chunk_text
chunks = chunk_text(book_text, chunk_size=8000, chunk_overlap=800)
store = RAGStore()
store.add(chunks, metadatas=[{"chapter": i} for i in range(len(chunks))])
# Búsqueda
for chunk, score in store.search("what does the protagonist decide?", top_k=5):
    print(f"  [{score:.3f}] {chunk.text[:100]}")
# Persist
store.save_to_dir(Path("./rag_store"))
```

### ScriptPlanner (`script_planner.py`)

2 pasos:
1. **Intent router**: clasifica la idea en `narrative | motion | montage` (criterio de ViMax)
2. **Plan**: usa el template específico de ese intent para generar el `NarrativeArc` (3 actos + logline + themes)

```python
from core.long_form import ScriptPlanner, LongFormIntent
planner = ScriptPlanner()
arc = await planner.plan(
    basic_idea="A retired racer trains a kid to win a championship",
    target_minutes=15.0,
    intent=LongFormIntent.MOTION,  # o None para auto-detectar
)
```

Templates portados de `ViMax/agents/script_planner.py` (MIT) — incluyen ejemplos largos de Wandering Earth, F-18, F1, montage de violinista.

### SceneExtractor + StoryboardArtist (`scenes.py`)

`SceneExtractor.extract(arc, characters)` → lista de Scenes con `setting + summary + characters + continuation_from_prev`.

`StoryboardArtist.draw_scene(scene, characters)` → lista de Shots con `visual_description + shot_type + camera_angle + camera_movement + speaker + dialogue + target_duration_s`.

`StoryboardArtist.decompose_visual(shot, characters)` → llena `first_frame_desc / last_frame_desc / motion_desc` (útil para i2v).

Templates portados de `ViMax/agents/scene_extractor.py` y `agents/storyboard_artist.py` (MIT).

### ReferenceImageSelector + BestImageSelector (`consistency.py`)

Trio que asegura consistency cross-shot:

**ReferenceImageSelector**: dada una lista de anchors (frames previos + portraits) y la descripción del shot a generar, selecciona ≤8 anchors a mostrar al image gen. 2-stage:
1. Text-only filter (rápido, descarta anchors irrelevantes)
2. Multimodal selection (manda las imágenes al VLM, elige las definitivas)

**BestImageSelector**: dados N candidatos generados del MISMO shot, VLM rankea por:
- Character consistency vs references (gender, age, features, clothing)
- Spatial consistency vs references (positions, layout, perspective)
- Description accuracy vs target text

Devuelve el path al mejor candidato.

```python
from core.long_form import ReferenceImageSelector, BestImageSelector
# Selection antes de generar
selector = ReferenceImageSelector()
chosen, guidance = await selector.select(anchors, "Aldo enters the library")
# Generar N candidatos, luego elegir el mejor
best = BestImageSelector()
path, reason = await best.select_best(candidates, chosen, "Aldo enters the library")
```

Prompts portados de `ViMax/agents/reference_image_selector.py` y `agents/best_image_selector.py` (MIT).

### Director (`director.py`)

Orquesta todo el pipeline. 2 fases separadas a propósito (cost vs gate humano):

```python
from core.long_form import Director
director = Director()

# Fase 1 — barato, gate humano en medio
script, job = await director.plan(
    source_text=text,
    source_kind="novel",
    target_minutes=30.0,
)
# ... revisar/editar script manualmente, persistido en working_dir/<job_id>/script.json ...

# Fase 2 — caro (requiere GPU)
job = await director.produce(job, script)
```

## Decisiones técnicas (vs ViMax wholesale)

| Componente ViMax | Decisión nuestra | Razón |
|---|---|---|
| LangChain `init_chat_model` | Reemplazado por `core.llm_router.complete_structured` | Cero duplicación con resto del pipeline |
| LangChain `ChatPromptTemplate + PydanticOutputParser` | Reemplazado por nativo Pydantic + httpx | Más liviano + sin breaking changes de LangChain |
| FAISS obligatorio | FAISS opt-in (`--extra longform-scale`) | Numpy basta hasta 5k chunks |
| `CacheBackedEmbeddings` (LangChain) | Cache nativo de HF/sentence-transformers | Mismo efecto, sin la dep |
| `langchain-text-splitters` `RecursiveCharacterTextSplitter` | **MANTENIDO** | Genuinamente mejor que casero (cascade ['\\n\\n', '\\n', '. ', ' ']) |
| `sentence-transformers` | **MANTENIDO** | Embeddings locales sin API cost recurring |
| Prompts canónicos | **PORTADOS** con atribución MIT | Representan horas de R&D del equipo HKU |

Resultado: ~200MB de overhead vs ~500MB de LangChain entero, sin perder calidad algorítmica.

## Cost model estimado

| Fase | Costo aprox | Tiempo aprox |
|---|---|---|
| Plan idea → arc | $0.02-0.10 | 30s-2min |
| Plan novel (50k words) → arc | $0.50-2.00 | 3-8min (compress N chunks en paralelo) |
| SceneExtractor (6-12 scenes) | $0.10-0.30 | 30s-1min |
| StoryboardArtist (30-100 shots) | $0.50-1.50 | 2-5min |
| **PHASE 1 total (book → script)** | **$1-4** | **5-15 min** |
| ReferenceImageSelector (per shot, VLM) | $0.02 | 5-10s |
| BestImageSelector (per shot, VLM con 3 candidates) | $0.04 | 5-10s |
| ComfyUI image gen × 3 candidates (per shot) | $0 (self-host) | 60-90s |
| Higgsfield DoP (per shot, 5s clip) | $0.20 | 30-60s |
| TTS narration (per shot) | $0.005 | 5s |
| **PHASE 2 per shot (con VLM checks + 3 candidates + motion)** | **~$0.27** | **~2 min** |
| **PHASE 2 total (60 shots × 10min video)** | **$15-20** | **30-60 min** |
| **Total end-to-end** | **$16-24** | **45-75 min** |

Para video de 30min con 150 shots: ~$45-50 total. Para una hora con 250 shots: ~$70-80.

## Estado actual (2026-06-15)

- ✅ Schemas: 8 modelos Pydantic (`LongFormScript`, `Scene`, `Shot`, `NarrativeArc`, etc)
- ✅ NovelCompressor con chunking + parallel LLM compress
- ✅ RAGStore híbrido numpy/FAISS con persistencia
- ✅ ScriptPlanner con intent routing
- ✅ SceneExtractor + StoryboardArtist + VisualDecompose
- ✅ ReferenceImageSelector + BestImageSelector (2-stage VLM)
- ✅ Director con `plan_long_form()` y `produce_long_form()` (stub)
- ✅ Entry point en `VideoParams.long_form_input`
- ✅ CLI: `contenido book plan/show/produce`
- ✅ 30 tests verde
- ⏳ `produce_long_form()` real (requiere GPU + ComfyUI corriendo para validar E2E)

## Crédito

Algoritmos, prompts canónicos y arquitectura conceptual de:
- **HKUDS/ViMax** — https://github.com/HKUDS/ViMax (MIT License)
- arXiv: **2606.07649** — *ViMax: Agentic Video Generation*

Nuestra integración:
- Reemplaza el stack LangChain wholesale por `core.llm_router` + Pydantic nativo
- Mantiene `langchain-text-splitters` (200KB aislado) por su calidad
- FAISS opt-in para escala
- Reusa el resto del pipeline (TTS, ComfyUI, editor, distribution)
- No duplica el novel2movie_pipeline.py (que en ViMax está marcado `# TODO: NOT IMPLEMENTED YET`)
