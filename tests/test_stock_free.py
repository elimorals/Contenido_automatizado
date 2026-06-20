"""Tests para los providers de corpus libre (ADR-022): parsers puros sobre payloads
representativos de cada API. La parte HTTP (async) es un wrapper fino no testeado
aquí (red); la lógica de parseo SÍ se cubre offline.

Providers:
- Archive.org (video, keyless, 2 pasos: search → metadata)
- Wikimedia Commons (video, keyless, 1 paso)
- NASA images-api (video, keyless, 2 pasos: search → asset manifest)
- Unsplash (IMAGEN, key, 1 paso → media_kind="image")
"""
from __future__ import annotations

from core.visual.stock.archive_org import _parse_length, _parse_metadata, _parse_search
from core.visual.stock.nasa import _parse_asset_manifest
from core.visual.stock.nasa import _parse_search as _nasa_search
from core.visual.stock.unsplash import _parse_unsplash
from core.visual.stock.wikimedia import _parse_wikimedia
from shared.schemas import VideoSource

# =============================================================================
# Archive.org
# =============================================================================


def test_archive_parse_search_identifiers():
    data = {"response": {"docs": [{"identifier": "A1"}, {"identifier": "A2"}]}}
    assert _parse_search(data) == ["A1", "A2"]


def test_archive_parse_search_empty():
    assert _parse_search({"response": {"docs": []}}) == []
    assert _parse_search({}) == []


def test_archive_parse_length_variants():
    assert _parse_length("30.5") == 30.5
    assert _parse_length("0:30") == 30.0
    assert _parse_length("1:02:03") == 3723.0
    assert _parse_length("") == 0.0
    assert _parse_length(None) == 0.0


def test_archive_parse_metadata_picks_mp4():
    data = {
        "metadata": {"title": "Ocean Footage"},
        "files": [
            {"name": "thumb.jpg", "format": "JPEG"},
            {"name": "ocean.mp4", "format": "h.264", "length": "30.5", "width": "1920", "height": "1080"},
        ],
    }
    mat = _parse_metadata("OceanItem", data, min_duration_s=3.0)
    assert mat is not None
    assert mat.provider == VideoSource.ARCHIVE_ORG
    assert mat.url == "https://archive.org/download/OceanItem/ocean.mp4"
    assert mat.duration_s == 30.5
    assert mat.width == 1920
    assert "Ocean Footage" in mat.description


def test_archive_parse_metadata_no_video_returns_none():
    data = {"metadata": {"title": "Photos"}, "files": [{"name": "a.jpg", "format": "JPEG"}]}
    assert _parse_metadata("X", data, min_duration_s=3.0) is None


def test_archive_parse_metadata_filters_short():
    data = {
        "metadata": {"title": "T"},
        "files": [{"name": "c.mp4", "format": "h.264", "length": "1.0"}],
    }
    assert _parse_metadata("X", data, min_duration_s=3.0) is None


# =============================================================================
# Wikimedia Commons
# =============================================================================


def test_wikimedia_parse_video():
    data = {
        "query": {
            "pages": {
                "12": {
                    "title": "File:Ocean waves.webm",
                    "imageinfo": [
                        {
                            "url": "https://upload.wikimedia.org/x/Ocean_waves.webm",
                            "width": 1920,
                            "height": 1080,
                            "mime": "video/webm",
                            "mediatype": "VIDEO",
                            "duration": 12.5,
                        }
                    ],
                }
            }
        }
    }
    mats = _parse_wikimedia(data, min_duration_s=3.0)
    assert len(mats) == 1
    assert mats[0].provider == VideoSource.WIKIMEDIA
    assert mats[0].url.endswith(".webm")
    assert mats[0].duration_s == 12.5
    assert "Ocean waves" in mats[0].description


def test_wikimedia_skips_non_video():
    data = {
        "query": {
            "pages": {
                "1": {
                    "title": "File:Photo.jpg",
                    "imageinfo": [{"url": "x.jpg", "mime": "image/jpeg", "mediatype": "BITMAP"}],
                }
            }
        }
    }
    assert _parse_wikimedia(data, min_duration_s=3.0) == []


def test_wikimedia_unknown_duration_kept():
    # Sin duration → no se filtra por min_duration (se asume válido).
    data = {
        "query": {
            "pages": {
                "1": {
                    "title": "File:Clip.ogv",
                    "imageinfo": [
                        {"url": "x.ogv", "width": 640, "height": 480, "mime": "application/ogg", "mediatype": "VIDEO"}
                    ],
                }
            }
        }
    }
    mats = _parse_wikimedia(data, min_duration_s=10.0)
    assert len(mats) == 1


# =============================================================================
# NASA
# =============================================================================


def test_nasa_parse_search():
    data = {
        "collection": {
            "items": [
                {
                    "href": "https://images-assets.nasa.gov/video/abc/collection.json",
                    "data": [{"title": "Earth", "description": "blue marble", "keywords": ["earth", "space"]}],
                }
            ]
        }
    }
    items = _nasa_search(data)
    assert len(items) == 1
    assert items[0]["href"].endswith("collection.json")
    assert items[0]["title"] == "Earth"
    assert "space" in items[0]["keywords"]


def test_nasa_parse_asset_manifest_prefers_mp4():
    urls = [
        "https://images-assets.nasa.gov/video/abc/abc~orig.mp4",
        "https://images-assets.nasa.gov/video/abc/abc~mobile.mp4",
        "https://images-assets.nasa.gov/video/abc/abc.srt",
    ]
    picked = _parse_asset_manifest(urls)
    assert picked.endswith(".mp4")


def test_nasa_parse_asset_manifest_no_mp4_none():
    assert _parse_asset_manifest(["a.srt", "b.json"]) is None


# =============================================================================
# Unsplash (imagen)
# =============================================================================


def test_unsplash_parse_image():
    data = {
        "results": [
            {
                "urls": {"regular": "https://images.unsplash.com/photo-1.jpg"},
                "width": 4000,
                "height": 6000,
                "description": "calm ocean at dawn",
                "alt_description": "sea",
                "tags": [{"title": "ocean"}, {"title": "water"}],
            }
        ]
    }
    mats = _parse_unsplash(data)
    assert len(mats) == 1
    m = mats[0]
    assert m.provider == VideoSource.UNSPLASH
    assert m.media_kind == "image"
    assert m.url.endswith(".jpg")
    assert "ocean" in m.tags
    assert "calm ocean" in m.description


def test_unsplash_falls_back_to_alt_description():
    data = {
        "results": [
            {"urls": {"regular": "x.jpg"}, "width": 10, "height": 10, "alt_description": "mountain"}
        ]
    }
    mats = _parse_unsplash(data)
    assert "mountain" in mats[0].description


def test_unsplash_empty():
    assert _parse_unsplash({"results": []}) == []


# =============================================================================
# Registry: inclusión de providers libres por flag (sin red)
# =============================================================================


def test_registry_includes_enabled_keyless(monkeypatch):
    import core.visual.stock.registry as reg
    from shared.config import Config

    cfg = Config()
    cfg.stock.archive_enabled = True
    cfg.stock.nasa_enabled = True
    monkeypatch.setattr(reg, "load_config", lambda: cfg)
    names = [p.name for p in reg.get_all_providers()]
    assert "archive_org" in names
    assert "nasa" in names
    assert "wikimedia" not in names  # flag off


def test_registry_excludes_disabled_free(monkeypatch):
    import core.visual.stock.registry as reg
    from shared.config import Config

    cfg = Config()  # libres off, sin keys
    monkeypatch.setattr(reg, "load_config", lambda: cfg)
    names = [p.name for p in reg.get_all_providers()]
    for n in ("archive_org", "wikimedia", "nasa", "unsplash"):
        assert n not in names
