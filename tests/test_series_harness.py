"""Tests del harness premisa → serie (ADR-039).

Cubre el casting de voces, el slug, la construcción de la biblia y la
idempotencia del harness. Cero red: el catálogo de voces y el LLM van mockeados.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from core.long_form.architect import _DraftBible, _DraftCharacter, create_bible, slugify
from core.long_form.voices import _catalog, canonical, cast_voices


@pytest.fixture(autouse=True)
def _fresh_catalog():
    """El catálogo se cachea; cada test parte de cero."""
    _catalog.cache_clear()
    yield
    _catalog.cache_clear()


def _fake_catalog() -> list[dict]:
    return [
        {"ShortName": "es-MX-DaliaNeural", "Gender": "Female", "Locale": "es-MX"},
        {"ShortName": "es-MX-JorgeNeural", "Gender": "Male", "Locale": "es-MX"},
        {"ShortName": "es-CO-SalomeNeural", "Gender": "Female", "Locale": "es-CO"},
        {"ShortName": "es-CO-GonzaloNeural", "Gender": "Male", "Locale": "es-CO"},
        {"ShortName": "es-ES-ElviraNeural", "Gender": "Female", "Locale": "es-ES"},
        {"ShortName": "en-US-AriaNeural", "Gender": "Female", "Locale": "en-US"},
    ]


class TestSlugify:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("La Herencia de los Vargas", "la-herencia-de-los-vargas"),
            ("Café Corazón — T1", "cafe-corazon-t1"),
            ("ÑOÑO!!", "nono"),
            ("   ", "serie"),
        ],
    )
    def test_slug_survives_filesystem(self, raw: str, expected: str) -> None:
        """El slug es directorio y clave de checkpoint: sin acentos ni signos."""
        assert slugify(raw) == expected

    def test_length_capped(self) -> None:
        assert len(slugify("palabra " * 40)) <= 60


class TestVoiceCasting:
    def test_assigns_distinct_voices(self, monkeypatch) -> None:
        monkeypatch.setattr(
            "core.long_form.voices._fetch_voices", _fake_catalog
        )
        cast = cast_voices([
            ("Ana", "female"), ("Bea", "female"),
            ("Luis", "male"), ("Iván", "male"),
        ])
        assert len(set(cast.values())) == 4

    def test_mexico_first(self, monkeypatch) -> None:
        """La primera voz de cada género debe ser mexicana."""
        monkeypatch.setattr("core.long_form.voices._fetch_voices", _fake_catalog)
        cast = cast_voices([("Ana", "female"), ("Luis", "male")])
        assert cast["Ana"].startswith("es-MX")
        assert cast["Luis"].startswith("es-MX")

    def test_spain_last(self, monkeypatch) -> None:
        """El acento peninsular desentona en un melodrama mexicano."""
        monkeypatch.setattr("core.long_form.voices._fetch_voices", _fake_catalog)
        cast = cast_voices([("A", "female"), ("B", "female"), ("C", "female")])
        assert cast["C"].startswith("es-ES")   # solo cuando ya no quedan latinas

    def test_recycles_with_warning_when_exhausted(self, monkeypatch) -> None:
        monkeypatch.setattr("core.long_form.voices._fetch_voices", _fake_catalog)
        cast = cast_voices([(f"P{i}", "male") for i in range(4)])
        assert len(cast) == 4                      # nadie se queda sin voz
        assert len(set(cast.values())) == 2        # solo hay 2 masculinas

    def test_unknown_gender_still_gets_a_voice(self, monkeypatch) -> None:
        """Una voz arbitraria es mejor que una vacía que rompa el TTS."""
        monkeypatch.setattr("core.long_form.voices._fetch_voices", _fake_catalog)
        assert cast_voices([("X", "no-binario")])["X"]

    def test_falls_back_without_network(self, monkeypatch) -> None:
        def boom() -> list[dict]:
            raise RuntimeError("sin red")

        monkeypatch.setattr("core.long_form.voices._fetch_voices", boom)
        cast = cast_voices([("Ana", "female")])
        assert cast["Ana"] == "es-MX-DaliaNeural-Female"

    def test_canonical_appends_gender_suffix(self) -> None:
        assert canonical("es-MX-DaliaNeural", "female") == "es-MX-DaliaNeural-Female"
        assert canonical("es-MX-JorgeNeural", "male") == "es-MX-JorgeNeural-Male"

    def test_works_inside_a_running_loop(self, monkeypatch) -> None:
        """Regresión: `create_bible` es async y llama a esto desde el loop.

        `asyncio.run()` lanza RuntimeError dentro de un loop; el except lo
        tragaba y caía al fallback de UNA voz por género — todos los personajes
        del mismo sexo acababan sonando igual, justo lo que el módulo evita.
        """
        monkeypatch.setattr("core.long_form.voices._fetch_voices", _fake_catalog)

        async def inside() -> dict[str, str]:
            return cast_voices([("A", "female"), ("B", "female")])

        cast = asyncio.run(inside())
        assert len(set(cast.values())) == 2


class TestCreateBible:
    def _draft(self) -> _DraftBible:
        return _DraftBible(
            slug="la-302",
            title="La habitación 302",
            logline="Una enfermera descubre que su paciente en coma mató a su hermano.",
            genre="melodrama de suspense",
            tone="Norteño, seco, tenso.",
            setting="Monterrey, hospital nocturno.",
            characters=[
                _DraftCharacter(
                    name="Lucía", gender="female",
                    appearance="Mujer de 31 años, uniforme quirúrgico azul, ojeras marcadas",
                    persona="Guarda un secreto.",
                ),
                _DraftCharacter(
                    name="Mateo", gender="male",
                    appearance="Hombre de 39 años, barba de días, bata de hospital",
                    persona="Despierta sin memoria aparente.",
                ),
            ],
            assets=[{"name": "Hab 302", "kind": "environment",
                     "description": "Cuarto de hospital con luz verde de monitor"}],
            rules=["Nadie más sabe quién es el paciente."],
        )

    @pytest.mark.asyncio
    async def test_builds_full_bible_from_premise(self, monkeypatch) -> None:
        monkeypatch.setattr("core.long_form.voices._fetch_voices", _fake_catalog)
        monkeypatch.setattr(
            "core.long_form.architect.complete_structured",
            AsyncMock(return_value=self._draft()),
        )
        bible = await create_bible("una enfermera y un paciente en coma")

        assert bible.slug == "la-302"
        assert len(bible.characters) == 2
        assert len(bible.assets) == 1
        assert bible.rules

    @pytest.mark.asyncio
    async def test_voices_assigned_by_pipeline_not_llm(self, monkeypatch) -> None:
        """El LLM alucina nombres de voces con total confianza.

        La primera biblia escrita a mano llevaba `es-MX-CarlotaNeural`, que no
        existe. El casting sale del catálogo real, nunca del modelo.
        """
        monkeypatch.setattr("core.long_form.voices._fetch_voices", _fake_catalog)
        monkeypatch.setattr(
            "core.long_form.architect.complete_structured",
            AsyncMock(return_value=self._draft()),
        )
        bible = await create_bible("premisa")

        voices = [c.voice for c in bible.characters]
        assert all(voices)
        assert len(set(voices)) == 2
        real = {v["ShortName"] for v in _fake_catalog()}
        assert all(v.rsplit("-", 1)[0] in real for v in voices)

    @pytest.mark.asyncio
    async def test_empty_premise_rejected(self) -> None:
        with pytest.raises(ValueError, match="premisa vacía"):
            await create_bible("   ")

    @pytest.mark.asyncio
    async def test_explicit_slug_wins(self, monkeypatch) -> None:
        monkeypatch.setattr("core.long_form.voices._fetch_voices", _fake_catalog)
        monkeypatch.setattr(
            "core.long_form.architect.complete_structured",
            AsyncMock(return_value=self._draft()),
        )
        bible = await create_bible("premisa", slug="Mi Serie Nueva")
        assert bible.slug == "mi-serie-nueva"

    @pytest.mark.asyncio
    async def test_unknown_asset_kind_normalized(self, monkeypatch) -> None:
        draft = self._draft()
        draft.assets[0].kind = "inventado"
        monkeypatch.setattr("core.long_form.voices._fetch_voices", _fake_catalog)
        monkeypatch.setattr(
            "core.long_form.architect.complete_structured",
            AsyncMock(return_value=draft),
        )
        bible = await create_bible("premisa")
        assert bible.assets[0].kind == "prop"


class TestHarnessIdempotence:
    @pytest.mark.asyncio
    async def test_reuses_existing_bible(self, tmp_path, monkeypatch) -> None:
        """Añadir episodios a una serie viva no debe reinventar los personajes.

        Regenerar la biblia rompería la consistencia de rostro que ADR-038
        existe para proteger.
        """
        from core.long_form import architect
        from shared.schemas import Episode, SeriesBible

        existing = SeriesBible(slug="viva", title="Serie Viva", logline="x" * 25)
        planned = Episode(
            number=1, title="Episodio uno", hook="algo pasa ya",
            summary="pasa algo importante", cliffhanger="suena el teléfono ahora",
        )

        created = AsyncMock()
        monkeypatch.setattr(architect, "create_bible", created)
        with (
            patch("core.long_form.series.bible_path") as bp,
            patch("core.long_form.series.load_bible", return_value=existing),
            patch("core.long_form.series.save_bible", return_value=tmp_path / "b.json"),
            patch("core.long_form.series.EpisodePlanner") as planner_cls,
        ):
            bp.return_value.exists.return_value = True
            planner_cls.return_value.plan = AsyncMock(return_value=planned)
            out = await architect.run_series(slug="viva", episodes=1, produce=False)

        created.assert_not_awaited()      # nunca se reescribió la biblia
        assert len(out.episodes) == 1

    @pytest.mark.asyncio
    async def test_new_series_needs_a_premise(self, monkeypatch) -> None:
        from core.long_form import architect

        with patch("core.long_form.series.bible_path") as bp:
            bp.return_value.exists.return_value = False
            with pytest.raises(ValueError, match="premisa"):
                await architect.run_series("", slug="nueva", episodes=1)

    @pytest.mark.asyncio
    async def test_plan_only_skips_production(self, tmp_path, monkeypatch) -> None:
        """El gate barato: escribir guiones sin gastar en generación visual."""
        from core.long_form import architect
        from shared.schemas import Episode, SeriesBible

        bible = SeriesBible(slug="s", title="Serie", logline="y" * 25)
        ep = Episode(number=1, title="Episodio uno", hook="pasa algo ya",
                     summary="resumen suficiente", cliffhanger="corta aquí mismo")
        produce = AsyncMock()

        with (
            patch("core.long_form.series.bible_path") as bp,
            patch("core.long_form.series.load_bible", return_value=bible),
            patch("core.long_form.series.save_bible", return_value=tmp_path / "b.json"),
            patch("core.long_form.series.EpisodePlanner") as planner_cls,
            patch("core.long_form.narrative_director.produce_episode", produce),
        ):
            bp.return_value.exists.return_value = True
            planner_cls.return_value.plan = AsyncMock(return_value=ep)
            await architect.run_series(slug="s", episodes=1, produce=False)

        produce.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_failed_episode_does_not_abort_the_batch(
        self, tmp_path, monkeypatch
    ) -> None:
        from core.long_form import architect
        from shared.schemas import Episode, SeriesBible

        bible = SeriesBible(slug="s", title="Serie", logline="z" * 25)
        planner = AsyncMock(side_effect=lambda bible, number, direction: Episode(
            number=number, title=f"Episodio {number}", hook="pasa algo ya",
            summary="resumen suficiente", cliffhanger="corta aquí mismo",
        ))

        with (
            patch("core.long_form.series.bible_path") as bp,
            patch("core.long_form.series.load_bible", return_value=bible),
            patch("core.long_form.series.save_bible", return_value=tmp_path / "b.json"),
            patch("core.long_form.series.EpisodePlanner") as planner_cls,
            patch(
                "core.long_form.narrative_director.produce_episode",
                AsyncMock(side_effect=RuntimeError("Sora 504")),
            ),
        ):
            bp.return_value.exists.return_value = True
            planner_cls.return_value.plan = planner
            out = await architect.run_series(slug="s", episodes=2, produce=True)

        # Los dos se planificaron aunque la producción falló en ambos.
        assert len(out.episodes) == 2
