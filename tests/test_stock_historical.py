"""Tests para los providers de corpus libre histórico (ADR-026): Library of
Congress, U.S. National Archives (NARA) y Pond5 Public Domain.

Mismo estilo que `test_stock_free` (ADR-022): se testean los **parsers puros**
con payloads representativos de cada API JSON; la capa HTTP async es wrapper
fino no testeado aquí. Los shapes se ajustan contra la API viva en E2E manual.
"""
from __future__ import annotations

from core.visual.stock.loc import _parse_loc
from core.visual.stock.nara import _parse_nara
from core.visual.stock.pond5_pd import _parse_pond5
from shared.schemas import VideoSource

# =============================================================================
# Library of Congress (files anidados como lista-de-listas)
# =============================================================================


def test_loc_parse_video_nested_files():
    data = {
        "results": [
            {
                "id": "http://www.loc.gov/item/abc/",
                "title": "Newsreel 1923",
                "description": ["historical newsreel reel"],
                "resources": [
                    {
                        "files": [
                            [
                                {"url": "https://tile.loc.gov/x/thumb.jpg", "mimetype": "image/jpeg"},
                                {
                                    "url": "https://tile.loc.gov/x/clip.mp4",
                                    "mimetype": "video/mp4",
                                    "width": 640,
                                    "height": 480,
                                },
                            ]
                        ]
                    }
                ],
            }
        ]
    }
    mats = _parse_loc(data, min_duration_s=3.0)
    assert len(mats) == 1
    assert mats[0].provider == VideoSource.LOC
    assert mats[0].url == "https://tile.loc.gov/x/clip.mp4"
    assert mats[0].width == 640 and mats[0].height == 480
    assert "Newsreel 1923" in mats[0].description


def test_loc_parse_picks_mp4_by_url_when_mimetype_missing():
    data = {
        "results": [
            {
                "title": "Silent Film",
                "resources": [{"files": [[{"url": "https://tile.loc.gov/x/film.mp4"}]]}],
            }
        ]
    }
    mats = _parse_loc(data, min_duration_s=3.0)
    assert len(mats) == 1
    assert mats[0].url.endswith(".mp4")


def test_loc_parse_no_video_returns_empty():
    data = {"results": [{"title": "Photos", "resources": [{"files": [[{"url": "a.jpg", "mimetype": "image/jpeg"}]]}]}]}
    assert _parse_loc(data, min_duration_s=3.0) == []


def test_loc_parse_empty():
    assert _parse_loc({"results": []}, min_duration_s=3.0) == []
    assert _parse_loc({}, min_duration_s=3.0) == []


# =============================================================================
# NARA (catalog.archives.gov v2)
# =============================================================================


def test_nara_parse_moving_image():
    data = {
        "results": [
            {
                "naId": "12345",
                "title": "Apollo 11 Launch",
                "scopeAndContentNote": "launch footage from KSC",
                "objects": [
                    {
                        "url": "https://catalog.archives.gov/x/apollo.mp4",
                        "mimeType": "video/mp4",
                        "width": 720,
                        "height": 480,
                        "duration": 45.0,
                    }
                ],
            }
        ]
    }
    mats = _parse_nara(data, min_duration_s=3.0)
    assert len(mats) == 1
    assert mats[0].provider == VideoSource.NARA
    assert mats[0].url.endswith("apollo.mp4")
    assert mats[0].duration_s == 45.0
    assert "Apollo 11" in mats[0].description


def test_nara_filters_short_when_duration_known():
    data = {
        "results": [
            {
                "naId": "1",
                "title": "T",
                "objects": [{"url": "x.mp4", "mimeType": "video/mp4", "duration": 1.0}],
            }
        ]
    }
    assert _parse_nara(data, min_duration_s=3.0) == []


def test_nara_skips_still_image():
    data = {
        "results": [
            {"naId": "1", "title": "Photo", "objects": [{"url": "x.jpg", "mimeType": "image/jpeg"}]}
        ]
    }
    assert _parse_nara(data, min_duration_s=3.0) == []


def test_nara_empty():
    assert _parse_nara({"results": []}, min_duration_s=3.0) == []
    assert _parse_nara({}, min_duration_s=3.0) == []


# =============================================================================
# Pond5 Public Domain (claves abreviadas v/dur/kw + alias largos)
# =============================================================================


def test_pond5_parse_short_keys():
    data = {
        "results": [
            {
                "id": "99",
                "title": "WWII Archival",
                "desc": "archival war footage",
                "kw": ["war", "1944"],
                "dur": 30.0,
                "v": "https://www.pond5.com/x/clip.mp4",
                "w": 1920,
                "h": 1080,
            }
        ]
    }
    mats = _parse_pond5(data, min_duration_s=3.0)
    assert len(mats) == 1
    assert mats[0].provider == VideoSource.POND5_PD
    assert mats[0].url.endswith("clip.mp4")
    assert mats[0].duration_s == 30.0
    assert mats[0].width == 1920
    assert "war" in mats[0].tags


def test_pond5_parse_long_alias_keys():
    data = {
        "results": [
            {
                "id": "5",
                "title": "Speech 1963",
                "duration": 12.0,
                "preview_url": "https://www.pond5.com/x/speech.mp4",
            }
        ]
    }
    mats = _parse_pond5(data, min_duration_s=3.0)
    assert len(mats) == 1
    assert mats[0].url.endswith("speech.mp4")


def test_pond5_filters_short():
    data = {"results": [{"id": "1", "title": "T", "dur": 1.0, "v": "x.mp4"}]}
    assert _parse_pond5(data, min_duration_s=3.0) == []


def test_pond5_no_url_skipped():
    data = {"results": [{"id": "1", "title": "T", "dur": 10.0}]}
    assert _parse_pond5(data, min_duration_s=3.0) == []


def test_pond5_empty():
    assert _parse_pond5({"results": []}, min_duration_s=3.0) == []
    assert _parse_pond5({}, min_duration_s=3.0) == []


# =============================================================================
# Registry: inclusión por flag keyless (sin red)
# =============================================================================


def test_registry_includes_enabled_historical(monkeypatch):
    import core.visual.stock.registry as reg
    from shared.config import Config

    cfg = Config()
    cfg.stock.loc_enabled = True
    cfg.stock.nara_enabled = True
    cfg.stock.pond5_pd_enabled = True
    monkeypatch.setattr(reg, "load_config", lambda: cfg)
    names = [p.name for p in reg.get_all_providers()]
    assert {"loc", "nara", "pond5_pd"}.issubset(set(names))


def test_registry_excludes_disabled_historical(monkeypatch):
    import core.visual.stock.registry as reg
    from shared.config import Config

    cfg = Config()
    monkeypatch.setattr(reg, "load_config", lambda: cfg)
    names = [p.name for p in reg.get_all_providers()]
    for n in ("loc", "nara", "pond5_pd"):
        assert n not in names
