"""Referencias por personaje en cuadro y assets de la biblia (auditoría 2026-09)."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from core.long_form.narrative_director import _assets_in, _on_screen_refs, _shot_prompt
from core.visual.generation.orchestrator import _generate_first_frame_with_fallback
from shared.schemas import (
    AssetProfile,
    Beat,
    BeatArtifact,
    BeatRole,
    BeatVisual,
    CharacterProfile,
    Scene,
    SeriesBible,
    Shot,
)


def _bible(tmp_path: Path) -> SeriesBible:
    chars = []
    for name in ("Renata", "Max", "Ximena"):
        p = tmp_path / f"{name}.jpg"
        p.write_bytes(b"x")
        chars.append(CharacterProfile(name=name, appearance=f"{name} look", portrait_path=str(p)))
    return SeriesBible(
        slug="s", title="Serie", logline="l", characters=chars,
        assets=[AssetProfile(name="Testamento", description="sobre sellado de cera roja")],
    )


def test_refs_follow_who_is_on_screen_not_the_voice(tmp_path):
    b = _bible(tmp_path)
    # Ximena habla por teléfono: no está en cuadro, su cara no debe entrar.
    shot = Shot(visual_description="Renata y Max en la cocina", speaker="Ximena",
                dialogue="¿Sigues ahí?", characters_present=["Renata", "Max"])
    assert [p.stem for p in _on_screen_refs(shot, b)] == ["Renata", "Max"]


def test_assets_match_even_when_llm_paraphrases(tmp_path):
    b = _bible(tmp_path)
    shot = Shot(visual_description="Max abre el testamento sobre la mesa de la cocina")
    scene = Scene(idx=0, title="Cocina", setting="Cocina, noche", summary="s",
                  environment="zumbido del refrigerador")
    assert [a.name for a in _assets_in(shot, scene, b)] == ["Testamento"]
    assert "sobre sellado de cera roja" in _shot_prompt(shot, scene, b)


async def test_frame_with_refs_goes_to_openai_edits_first(tmp_path):
    ref = tmp_path / "r.jpg"
    ref.write_bytes(b"x")
    frame = tmp_path / "f.jpg"
    frame.write_bytes(b"x")
    oai = MagicMock()
    oai.generate = AsyncMock(return_value=BeatArtifact(idx=0, first_frame_path=frame))
    gemini = MagicMock()
    gemini.generate = AsyncMock()
    beat = Beat(idx=0, role=BeatRole.HOOK, text="t", target_duration_s=4, veo_duration=4)
    visual = BeatVisual(image_prompt="p", visual_anchor="a", character_refs=[ref])

    path, real, _ = await _generate_first_frame_with_fallback(
        None, None, gemini, oai, beat, visual, "general", tmp_path
    )
    assert path == frame and real
    gemini.generate.assert_not_called()


async def test_openai_edits_sends_refs_as_multipart(tmp_path, monkeypatch):
    import base64

    from core.visual.generation import openai_image

    ref = tmp_path / "r.jpg"
    ref.write_bytes(b"jpg")
    sent = {}

    class _Resp:
        status_code = 200

        def json(self):
            return {"data": [{"b64_json": base64.b64encode(b"png").decode()}]}

    class _Client:
        def __init__(self, *a, **k): ...
        async def __aenter__(self): return self
        async def __aexit__(self, *a): ...
        async def post(self, url, headers, **kw):
            sent.update(url=url, **kw)
            return _Resp()

    monkeypatch.setattr(openai_image.httpx, "AsyncClient", _Client)
    out = await openai_image._call_openai_image(prompt="p", model="m", api_key="k", refs=[ref])
    assert out == b"png"
    assert sent["url"].endswith("/images/edits")
    assert sent["files"][0][0] == "image[]"
