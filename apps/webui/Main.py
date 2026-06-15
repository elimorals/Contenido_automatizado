"""Streamlit WebUI — consume API FastAPI vía httpx.

Esta WebUI NO importa core/ ni pipeline directamente.
Habla únicamente con la API HTTP en `CONTENIDO_API_URL` (default http://localhost:8000).
Si la API está caída, la WebUI degrada gracefully.
"""
from __future__ import annotations

import os
import time
from typing import Any

import httpx
import streamlit as st

# ---------------------------------------------------------------------------
# Page config (debe ir antes de cualquier otro st.* call)
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="contenido — generador de reels",
    page_icon="🎬",
    layout="wide",
    initial_sidebar_state="expanded",
)


# ---------------------------------------------------------------------------
# API client helpers
# ---------------------------------------------------------------------------
DEFAULT_API_URL = os.getenv("CONTENIDO_API_URL", "http://localhost:8000")
POLL_INTERVAL_S = 2
POLL_MAX_ITERS = 180  # 6 minutos máx


def get_api_url() -> str:
    """Obtiene la URL de la API desde session_state o env."""
    return st.session_state.get("api_url") or DEFAULT_API_URL


def api_post(path: str, json_body: dict[str, Any], timeout: float = 30.0) -> dict[str, Any]:
    """POST a la API. Lanza httpx.HTTPError si falla."""
    with httpx.Client(timeout=timeout) as client:
        r = client.post(f"{get_api_url()}{path}", json=json_body)
        r.raise_for_status()
        return r.json()


def api_get(path: str, timeout: float = 10.0) -> dict[str, Any]:
    """GET a la API. Lanza httpx.HTTPError si falla."""
    with httpx.Client(timeout=timeout) as client:
        r = client.get(f"{get_api_url()}{path}")
        r.raise_for_status()
        return r.json()


def _friendly_error(e: Exception) -> str:
    """Convierte excepción en mensaje user-friendly."""
    if isinstance(e, httpx.ConnectError):
        return f"No se pudo conectar con la API en {get_api_url()}. ¿Está levantada?"
    if isinstance(e, httpx.TimeoutException):
        return "La API tardó demasiado en responder (timeout)."
    if isinstance(e, httpx.HTTPStatusError):
        try:
            detail = e.response.json().get("detail", e.response.text[:200])
        except Exception:
            detail = e.response.text[:200]
        return f"API respondió {e.response.status_code}: {detail}"
    return f"Error inesperado: {e}"


# ---------------------------------------------------------------------------
# i18n minimal (solo labels UI, no afecta el script generado)
# ---------------------------------------------------------------------------
LANGUAGES = [
    "es-MX",
    "en-US",
    "zh-CN",
    "fr-FR",
    "de-DE",
    "pt-BR",
    "it-IT",
    "ja-JP",
    "ko-KR",
    "ru-RU",
]

EDGE_VOICES = [
    "en-US-AvaNeural-Female",
    "en-US-AndrewNeural-Male",
    "en-US-EmmaNeural-Female",
    "es-MX-DaliaNeural-Female",
    "es-MX-JorgeNeural-Male",
    "es-ES-ElviraNeural-Female",
    "zh-CN-XiaoxiaoNeural-Female",
    "zh-CN-YunxiNeural-Male",
    "fr-FR-DeniseNeural-Female",
    "de-DE-KatjaNeural-Female",
    "pt-BR-FranciscaNeural-Female",
    "it-IT-ElsaNeural-Female",
    "ja-JP-NanamiNeural-Female",
    "ko-KR-SunHiNeural-Female",
]


# ---------------------------------------------------------------------------
# Pipeline runner: encola + polling + display
# ---------------------------------------------------------------------------
def _run_and_show(params: dict[str, Any]) -> None:
    """POST /videos + polling a /tasks/{task_id} + render final."""
    try:
        with st.spinner("Encolando trabajo..."):
            resp = api_post("/videos", params)
    except Exception as e:
        st.error(_friendly_error(e))
        return

    data = resp.get("data", {}) if isinstance(resp, dict) else {}
    task_id = data.get("task_id") or data.get("id")
    if not task_id:
        st.error("La API no devolvió task_id. Respuesta inesperada.")
        st.json(resp)
        return

    st.info(f"📋 Task ID: `{task_id}`")

    progress = st.progress(0, text="Esperando inicio...")
    timings_placeholder = st.empty()

    for iteration in range(POLL_MAX_ITERS):
        try:
            info_resp = api_get(f"/tasks/{task_id}")
        except Exception as e:
            # No abortar al primer fallo: la API puede estar reiniciando
            st.warning(f"Reintentando consulta de estado: {_friendly_error(e)}")
            time.sleep(POLL_INTERVAL_S)
            continue

        info = info_resp.get("data", {}) if isinstance(info_resp, dict) else {}
        state = info.get("state", 0)
        prog = int(info.get("progress", 0) or 0)
        prog = max(0, min(100, prog))

        # Render progress + timings parciales
        progress.progress(
            prog if state >= 0 else 0,
            text=f"Estado: {state} — progreso: {prog}%",
        )
        timings = info.get("timings_s", {}) or {}
        if timings:
            with timings_placeholder.container():
                with st.expander("⏱ Timings parciales"):
                    st.json(timings)

        # COMPLETE
        if state == 1:
            progress.progress(100, text="✓ Completado")
            videos = info.get("videos") or []
            total_s = (info.get("timings_s") or {}).get("total", 0)
            st.success(f"🎉 Reel generado en {float(total_s):.1f}s")

            if videos:
                try:
                    st.video(videos[0])
                except Exception:
                    st.warning(f"No se pudo previsualizar el video: {videos[0]}")
                # Botones de descarga (si los paths son accesibles HTTP)
                for i, v in enumerate(videos):
                    st.markdown(f"- 🎞 Video {i + 1}: `{v}`")

            with st.expander("📊 Timings completos"):
                st.json(info.get("timings_s", {}))
            with st.expander("💰 Cost breakdown"):
                st.json(info.get("cost_breakdown", {}))
            script_txt = info.get("script") or info.get("script_text") or ""
            if script_txt:
                with st.expander("📜 Script generado"):
                    st.code(script_txt)
            return

        # FAILED
        if state == -1:
            progress.empty()
            err = (info.get("timings_s") or {}).get("error") or info.get("error") or "desconocido"
            st.error(f"❌ Pipeline falló: {err}")
            with st.expander("Detalle completo"):
                st.json(info)
            return

        time.sleep(POLL_INTERVAL_S)

    # Timeout
    st.warning(
        f"⚠ Timeout de polling ({POLL_MAX_ITERS * POLL_INTERVAL_S}s). "
        f"La task `{task_id}` puede seguir en progreso. "
        f"Consulta su estado con `GET {get_api_url()}/tasks/{task_id}`."
    )


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------
with st.sidebar:
    st.title("🎬 contenido")
    st.caption("Fusión MoneyPrinterTurbo × reels-af")

    # Persist API URL en session_state
    st.session_state["api_url"] = st.text_input(
        "API URL",
        value=st.session_state.get("api_url", DEFAULT_API_URL),
        help="Endpoint de la API FastAPI. Cambia esto si la corres en otro host/puerto.",
    )

    if st.button("🔍 Verificar API", use_container_width=True):
        try:
            health = api_get("/health")
            data = health.get("data", health) if isinstance(health, dict) else {}
            ver = data.get("version") or data.get("api_version") or "?"
            phase = data.get("phase") or "?"
            st.success(f"✅ API OK — v{ver}, phase: {phase}")
        except Exception as e:
            st.error(_friendly_error(e))

    st.divider()

    st.session_state["ui_language"] = st.selectbox(
        "Idioma UI",
        LANGUAGES,
        index=LANGUAGES.index(st.session_state.get("ui_language", "es-MX"))
        if st.session_state.get("ui_language", "es-MX") in LANGUAGES
        else 0,
        help="Solo cambia labels de la UI. No afecta el idioma del script generado.",
    )

    st.session_state["default_mode"] = st.radio(
        "Modo default",
        ["express", "premium"],
        index=1,
        horizontal=True,
    )

    st.divider()
    st.caption(
        "💡 La WebUI consume la API HTTP. "
        "Asegúrate de que `uvicorn apps.api.main:app` esté corriendo."
    )


# ---------------------------------------------------------------------------
# Main tabs
# ---------------------------------------------------------------------------
tab_express, tab_premium, tab_avanzado = st.tabs(
    ["⚡ Express", "🎯 Premium", "🔬 Avanzado"]
)


# === EXPRESS ===
with tab_express:
    st.header("Reel rápido (MPT clásico)")
    st.caption(
        "⏱ 3-5 min · 💰 $0.001-0.05 por reel · Stock footage + Edge TTS + SRT subtítulos."
    )

    subject = st.text_input(
        "Subject (tema del reel)",
        placeholder="ej. Spring flowers in Tokyo",
        key="exp_subject",
    )

    col1, col2, col3 = st.columns([1, 1, 1])
    with col1:
        exp_aspect = st.radio(
            "Aspect ratio",
            ["9:16", "16:9", "1:1"],
            index=0,
            horizontal=True,
            key="exp_aspect",
        )
    with col2:
        exp_voice = st.selectbox("Voz (Edge TTS)", EDGE_VOICES, index=0, key="exp_voice")
    with col3:
        exp_subtitle_style = st.selectbox(
            "Estilo subtítulos",
            ["word_burst", "srt"],
            index=0,
            key="exp_subtitle_style",
            help="word_burst: palabra por palabra animado. srt: subtítulos clásicos.",
        )

    col4, col5, col6 = st.columns([1, 1, 1])
    with col4:
        exp_count = st.slider("# videos", 1, 5, 1, key="exp_count")
    with col5:
        exp_paragraphs = st.slider("# párrafos", 1, 10, 3, key="exp_paragraphs")
    with col6:
        exp_bgm = st.selectbox(
            "BGM (música)",
            ["random", "none", "custom"],
            index=0,
            key="exp_bgm",
        )

    exp_bgm_file = None
    if exp_bgm == "custom":
        exp_bgm_file = st.file_uploader("Sube tu BGM (.mp3)", type=["mp3"], key="exp_bgm_file")

    if st.button(
        "🎬 Generar Express",
        type="primary",
        disabled=not subject,
        use_container_width=True,
    ):
        params = {
            "subject": subject,
            "mode": "express",
            "aspect": exp_aspect,
            "voice_name": exp_voice,
            "video_count": exp_count,
            "paragraph_number": exp_paragraphs,
            "subtitle_style": exp_subtitle_style,
            "bgm_mode": exp_bgm,
        }
        _run_and_show(params)


# === PREMIUM ===
with tab_premium:
    st.header("Reel premium (DAG profundo)")
    st.caption(
        "⏱ 70-110s · 💰 $0.08/reel ken-burns · $1.20/reel con Veo · "
        "18 reasoners narrativos · critic gating."
    )

    prem_topic_tab, prem_article_tab = st.tabs(["📌 Por tema", "🔗 Por URL"])

    # --- Topic ---
    with prem_topic_tab:
        topic = st.text_input(
            "Topic",
            placeholder="ej. the placebo effect",
            key="prem_topic",
        )

        col1, col2 = st.columns(2)
        with col1:
            prem_aspect_t = st.radio(
                "Aspect ratio",
                ["9:16", "16:9", "1:1"],
                index=0,
                horizontal=True,
                key="prem_aspect_t",
            )
            prem_strategy_t = st.selectbox(
                "Visual strategy",
                ["hybrid", "stock", "ia"],
                index=0,
                key="prem_strat_t",
                help="hybrid: mezcla stock + IA. stock: solo Pexels/Pixabay. ia: solo generado.",
            )
            prem_voice_t = st.selectbox(
                "Voz",
                EDGE_VOICES,
                index=0,
                key="prem_voice_t",
            )
        with col2:
            prem_hunter_temp = st.slider(
                "Hunter temperature",
                0.0,
                2.0,
                1.1,
                step=0.1,
                key="prem_hunter_temp_t",
                help="Más alto = más creativo/diverso entre hunters.",
            )
            prem_critic_depth = st.slider(
                "Critic depth (candidates)",
                1,
                12,
                4,
                key="prem_critic_depth_t",
            )
            prem_narrator_count = st.slider(
                "# narradores",
                1,
                3,
                1,
                key="prem_narrator_count_t",
            )

        prem_use_veo_t = st.checkbox(
            "🎥 Usar Veo i2v (+$1.10/reel)",
            value=False,
            key="prem_veo_t",
            help="Genera shots con Google Veo image-to-video. Sube mucho el costo.",
        )
        if prem_use_veo_t:
            st.warning(
                "⚠ Veo i2v añade ~$1.10 al costo del reel. "
                "Asegúrate de tener `GOOGLE_API_KEY` configurada en la API."
            )

        if st.button(
            "🎯 Generar Premium (topic)",
            type="primary",
            disabled=not topic,
            use_container_width=True,
        ):
            params = {
                "topic": topic,
                "mode": "premium",
                "aspect": prem_aspect_t,
                "visual_strategy": prem_strategy_t,
                "voice_name": prem_voice_t,
                "use_veo": prem_use_veo_t,
                "hunter_temperature": prem_hunter_temp,
                "critic_depth": prem_critic_depth,
                "narrator_count": prem_narrator_count,
            }
            _run_and_show(params)

    # --- Article URL ---
    with prem_article_tab:
        url = st.text_input(
            "URL del artículo",
            placeholder="https://arxiv.org/abs/2509.25541",
            key="prem_url",
        )

        col1, col2 = st.columns(2)
        with col1:
            prem_aspect_a = st.radio(
                "Aspect ratio",
                ["9:16", "16:9", "1:1"],
                index=0,
                horizontal=True,
                key="prem_aspect_a",
            )
            prem_strategy_a = st.selectbox(
                "Visual strategy",
                ["hybrid", "stock", "ia"],
                index=0,
                key="prem_strat_a",
            )
        with col2:
            prem_voice_a = st.selectbox("Voz", EDGE_VOICES, index=0, key="prem_voice_a")
            prem_use_veo_a = st.checkbox(
                "🎥 Usar Veo i2v (+$1.10/reel)",
                value=False,
                key="prem_veo_a",
            )

        st.caption("ℹ Modo URL usa 10 reasoners (vs 18 en topic mode).")

        if st.button(
            "🎯 Generar Premium (article)",
            type="primary",
            disabled=not url,
            use_container_width=True,
        ):
            params = {
                "url": url,
                "mode": "premium",
                "aspect": prem_aspect_a,
                "visual_strategy": prem_strategy_a,
                "voice_name": prem_voice_a,
                "use_veo": prem_use_veo_a,
            }
            _run_and_show(params)


# === AVANZADO ===
with tab_avanzado:
    st.header("Endpoints individuales")
    st.caption("Debug y testing de fases aisladas del pipeline.")

    sub_tab_script, sub_tab_hunters, sub_tab_audio, sub_tab_health = st.tabs(
        ["📝 Script", "🎯 Hunters", "🔊 Audio", "❤️ Health"]
    )

    # --- Solo script ---
    with sub_tab_script:
        st.subheader("Generar solo el script (sin medios)")
        s_input = st.text_input(
            "Topic o URL",
            placeholder="ej. quantum entanglement",
            key="solo_script_in",
        )
        s_mode = st.radio(
            "Modo",
            ["premium", "express"],
            index=0,
            horizontal=True,
            key="solo_script_mode",
        )
        if st.button("Generar script", disabled=not s_input, key="btn_solo_script"):
            if s_input.startswith(("http://", "https://")):
                params = {"url": s_input, "mode": s_mode}
            else:
                params = {"topic": s_input, "mode": s_mode}
            with st.spinner("Generando ScriptDraft..."):
                try:
                    resp = api_post("/scripts", params, timeout=120.0)
                    st.success("✓ Script generado")
                    st.json(resp.get("data", {}))
                except Exception as e:
                    st.error(_friendly_error(e))

    # --- Solo hunters ---
    with sub_tab_hunters:
        st.subheader("Correr solo los hunters")
        st.caption("4 hunters paralelos × 3 candidates = 12 ángulos narrativos.")
        topic_h = st.text_input(
            "Topic",
            placeholder="ej. dark matter",
            key="solo_hunters_topic",
        )
        if st.button(
            "Correr hunters",
            disabled=not topic_h,
            key="btn_solo_hunters",
        ):
            with st.spinner("4 hunters en paralelo..."):
                try:
                    resp = api_post(
                        "/hunters",
                        {"topic": topic_h, "mode": "premium"},
                        timeout=180.0,
                    )
                    data = resp.get("data", {})
                    candidates = data.get("candidates", [])
                    st.success(f"✓ {len(candidates)} candidates generados")
                    if candidates:
                        st.dataframe(candidates, use_container_width=True)
                    else:
                        st.json(data)
                except Exception as e:
                    st.error(_friendly_error(e))

    # --- Solo audio ---
    with sub_tab_audio:
        st.subheader("Generar solo el audio (TTS)")
        a_text = st.text_area(
            "Texto a sintetizar",
            placeholder="Hola, este es un test de Edge TTS.",
            key="solo_audio_text",
            height=120,
        )
        a_voice = st.selectbox(
            "Voz",
            EDGE_VOICES,
            index=0,
            key="solo_audio_voice",
        )
        if st.button("Generar audio", disabled=not a_text, key="btn_solo_audio"):
            with st.spinner("Sintetizando con Edge TTS..."):
                try:
                    resp = api_post(
                        "/audio",
                        {"subject": a_text, "voice_name": a_voice},
                        timeout=120.0,
                    )
                    data = resp.get("data", {})
                    audio_path = data.get("audio_path") or data.get("audio_file")
                    if audio_path:
                        try:
                            st.audio(audio_path)
                        except Exception:
                            st.warning(f"No se pudo reproducir, path: {audio_path}")

                    word_timings = data.get("word_timings") or data.get("subtitles")
                    if word_timings:
                        with st.expander("📝 Word timings"):
                            st.dataframe(word_timings, use_container_width=True)

                    with st.expander("Respuesta completa"):
                        st.json(data)
                except Exception as e:
                    st.error(_friendly_error(e))

    # --- Health ---
    with sub_tab_health:
        st.subheader("Health check de la API")
        if st.button("Consultar /health", key="btn_health"):
            try:
                resp = api_get("/health")
                st.success("✅ API responde")
                st.json(resp)
            except Exception as e:
                st.error(_friendly_error(e))

        if st.button("Listar tasks recientes", key="btn_tasks"):
            try:
                resp = api_get("/tasks")
                data = resp.get("data", resp)
                st.json(data)
            except Exception as e:
                st.error(_friendly_error(e))


# ---------------------------------------------------------------------------
# Footer
# ---------------------------------------------------------------------------
st.divider()
st.caption(
    f"🔌 API: `{get_api_url()}` · "
    f"🌐 UI lang: `{st.session_state.get('ui_language', 'es-MX')}` · "
    "📚 Docs: `/docs` en la API"
)
