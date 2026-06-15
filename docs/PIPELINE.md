# Pipeline detallado

Ver [`../ARCHITECTURE.md`](../ARCHITECTURE.md) para el overview.

## Modo Premium — topic_to_reel (18 reasoners)

### Fase 1: Hunters (4 reasoners paralelos, ~8s, temp=1.1)

Cada hunter recibe el topic y produce 3 EssenceCandidate con su ángulo único.

| Hunter | Ángulo | Output |
|---|---|---|
| `hunt_specific_figure` | Persona desconocida + año + hallazgo | 3 candidates |
| `hunt_reversal` | Interpretación común está invertida | 3 candidates |
| `hunt_temporal` | Año/evento específico reenmarca el campo | 3 candidates |
| `hunt_cross_domain` | Bridge inesperado a otro campo | 3 candidates |

**Anti-clichés explícitos**: Plato's cave, trolley problem, quantum entanglement, etc. están baneados por prompt.

### Fase 2: Critic (1 reasoner, ~4s, temp=0.5)

Recibe 12 candidates → devuelve top 3 + rankings.

**Scoring**:
- `novelty` (1-10): ¿Cuántos lo han escuchado?
- `specificity` (1-10): ¿Nombre/año/número?
- `hookability` (1-10): ¿Stop-the-scroll en <1s?
- `narratability` (1-10): ¿Works as 25-30s spoken story?
- `composite` (1-10): weighted (no mean)

**Diversity rule**: si top 2 son `specific_figure`, prefiere otro ángulo para slot 3.

### Fase 3: Narrators (3 reasoners paralelos, ~8s, temp=0.7)

Cada essence → 1 ConversationalScript con delayed-reveal.

**Estructura**:
- `tease` (5-15 words, ~2s): curiosity gap, NO answer/name/year
- `common_belief` (optional): what most assume → sets up flip
- `reveal` (2-3 sentences, ~15-18s): NAMED person, years, mechanism
- `payoff` (1 sentence, ~3-5s): callback al tease

**Open styles**: question, setup_flip, cryptic_setup, topic_tease, personal_stake.

**Tag discipline**: ≤2 tags ([curious] + [emphasis]).

### Fase 4: Judge (1 reasoner, ~3s, temp=0.5)

Pairwise comparison (no scoring absoluto) → winner_idx + composite_score + why.

**Priority order**:
1. Hook strength (scroll-stop en <1s?)
2. Specificity (named entity / number / year)
3. Loop-back execution
4. Trope avoidance ("studies show", "did you know")
5. Spoken flow (varied length)
6. Stake/relevance

### Fase 5: Adapt (determinístico, sin LLM)

ConversationalScript → ScriptDraft (mapping de campos):
- `tease` → `hook`
- `reveal` (split por sentence) → `mechanism_lines`
- `payoff` → `payoff_line`
- `narration` → `narration` (con tags)

Pasa por validator de loop-back (puede fallar → retry con narrator).

### Fase 6: Audio (1 reasoner, ~12s)

`core/tts/timing.py`:
1. Split narration por sentence (mantiene tags inline)
2. Per-sentence parallel TTS (engine elegido)
3. ffprobe measure cada WAV
4. atempo=1.35 (preserve pitch)
5. Native WAV concat sin drift
6. Word distribution por syllable count

Output: `AudioArtifact(path, duration_s, word_timings)`.

### Fase 7: Plan (paralelo, ~1s, determinístico)

- `pack_cards(word_timings)` → list[Card] (layout subtítulos)
- `plan_beats(script, audio_duration_s)` → list[Beat] (Veo buckets 4/6/8s)

### Fase 8: Visual+Accent (paralelo, ~7s)

- `plan_beat_visuals(beats, essence, narration)` → list[BeatVisual]
  - image_prompt grounded en evidence
  - motion_hint según beat.role
  - visual_anchor (qué evidence ancla el visual)
- `plan_beat_accents(beats, essence)` → list[AccentOverlay | None]
  - Biased a None (sobrecuso > underuse)
  - 6 patrones canónicos

### Fase 9: Media (per-beat, ~38s — bottleneck)

`core/visual/selector.py` decide per beat:
- Stock (Pexels/Pixabay/Coverr) — barato
- IA (Gemini Image first frame + Veo i2v) — premium
- Mixto — primer plano IA + cortes de stock

**Two-tier fallback**:
- Image gen fail → solid color placeholder + beat idx
- Veo fail → ken-burns con first frame

### Fase 10: Stitch (single ffmpeg, ~5s)

UNA invocación de ffmpeg:
```
ffmpeg \
  -f concat -safe 0 -i filelist.txt \
  -i full_audio.wav \
  -i bgm.mp3 \
  -filter_complex "
    [0:v]scale=1080:1920,setsar=1[v0];
    [v0]ass=global.ass[v];
    [1:a][2:a]amix=inputs=2:weights=1 0.2[a]
  " \
  -map "[v]" -map "[a]" \
  -c:v libx264 -preset fast -crf 23 \
  -c:a aac -b:a 128k \
  -r 30 \
  output.mp4
```

**Por qué ONE invocation**:
- Concat filter es sample-accurate (no intermediate file)
- Audio prima UNA vez (no per-shot drift)
- libass burn en canvas res final
- BGM mix en mismo pass

### Output

```
output/topic-{uuid}/
├── reel.mp4              # 1080×1920, 20-25s, H.264+AAC
└── result.json
    ├── source: "topic"
    ├── topic: "..."
    ├── chosen_essence: {...}
    ├── winner_composite: 8.4
    ├── narration: "..."
    ├── timings_s: {hunt, critic, narrate, judge, tts, plan, visual_accent, media, stitch, total}
    └── cost_breakdown: {llm, tts, image, video, total}
```

---

## Modo Express — subject_to_reel (MPT clásico)

### Fase 1: Script (1 LLM call, ~3s)
`llm.generate_script(subject, language, paragraph_number)` → narration string.

### Fase 2: Terms (1 LLM call, ~2s)
`llm.generate_terms(subject, script, amount=5)` → list[str].

### Fase 3: Audio (~10s)
`core/tts` con engine elegido. Sample-accurate timing (gracias al ADR-005).

### Fase 4: Subtitle (~5s)
Edge SubMaker (gratis) o Whisper (más preciso).

### Fase 5: Materials (~30s)
`core/visual/stock` paralelo:
- Pexels search por cada term → top 5
- Validate con ffprobe (duration > 0, fps > 0)
- Cache local por hash

### Fase 6: Stitch (~60s)
Same single-pass ffmpeg del modo premium, pero:
- Modo concat = `random` o `sequential`
- Sin accents (default)
- BGM opcional
- Subtítulos SRT o word-burst (elegible)

### Output

```
output/subject-{uuid}/
├── final-1.mp4           # video principal
├── final-2.mp4           # si video_count>1
├── script.json
├── audio.mp3
├── subtitle.srt
└── materials/
    ├── vid-{hash1}.mp4
    └── ...
```

Tiempo total: 3-5min. Costo: ~$0.01-0.05.
