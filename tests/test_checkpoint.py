"""Tests para core/checkpoint: persistencia reanudable por task (ADR-024).

Store JSON atómico que registra fases completadas + artefactos por beat, para que
un re-run salte trabajo ya hecho (útil en long-form; marginal en reels de 25s).
Lógica pura sobre el filesystem — testeable con tmp_path.
"""
from __future__ import annotations

from core.checkpoint import Checkpoint, CheckpointStore, get_checkpoint_store


def test_new_checkpoint_is_empty(tmp_path):
    store = CheckpointStore(tmp_path)
    cp = store.load("task1")
    assert isinstance(cp, Checkpoint)
    assert cp.task_id == "task1"
    assert cp.phases == {}
    assert cp.beats == {}


def test_mark_and_query_phase(tmp_path):
    store = CheckpointStore(tmp_path)
    cp = store.load("t")
    assert cp.is_phase_done("tts") is False
    cp.mark_phase("tts", {"audio_path": "/a.wav"})
    assert cp.is_phase_done("tts") is True
    assert cp.phase_data("tts")["audio_path"] == "/a.wav"


def test_persist_roundtrip(tmp_path):
    store = CheckpointStore(tmp_path)
    cp = store.load("t")
    cp.mark_phase("tts", {"duration_s": 21.0})
    cp.mark_beat(0, {"video_path": "/b0.mp4", "source": "pexels"})
    store.save(cp)

    # Nuevo store sobre el mismo dir → ve lo persistido.
    store2 = CheckpointStore(tmp_path)
    cp2 = store2.load("t")
    assert cp2.is_phase_done("tts") is True
    assert cp2.phase_data("tts")["duration_s"] == 21.0
    assert cp2.beat_done(0) is True
    assert cp2.beat_data(0)["source"] == "pexels"


def test_beats_independent(tmp_path):
    store = CheckpointStore(tmp_path)
    cp = store.load("t")
    cp.mark_beat(2, {"video_path": "/b2.mp4"})
    assert cp.beat_done(2) is True
    assert cp.beat_done(0) is False
    assert cp.pending_beats(total=4) == [0, 1, 3]


def test_pending_beats_all_when_none_done(tmp_path):
    store = CheckpointStore(tmp_path)
    cp = store.load("t")
    assert cp.pending_beats(total=3) == [0, 1, 2]


def test_clear_removes_file(tmp_path):
    store = CheckpointStore(tmp_path)
    cp = store.load("t")
    cp.mark_phase("tts")
    store.save(cp)
    assert store.exists("t") is True
    store.clear("t")
    assert store.exists("t") is False
    # load tras clear → checkpoint vacío de nuevo.
    assert store.load("t").is_phase_done("tts") is False


def test_corrupt_file_yields_empty_checkpoint(tmp_path):
    store = CheckpointStore(tmp_path)
    (tmp_path / "bad.checkpoint.json").write_text("{not valid json")
    cp = store.load("bad")  # no debe crashear
    assert cp.phases == {}


def test_save_is_atomic_no_partial(tmp_path):
    # Tras save no debe quedar archivo .tmp suelto.
    store = CheckpointStore(tmp_path)
    cp = store.load("t")
    cp.mark_phase("plan")
    store.save(cp)
    tmps = list(tmp_path.glob("*.tmp"))
    assert tmps == []


def test_get_checkpoint_store_explicit_dir(tmp_path):
    store = get_checkpoint_store(tmp_path)
    assert isinstance(store, CheckpointStore)
    assert store.base_dir == tmp_path


def test_get_checkpoint_store_default_uses_config():
    # Sin base_dir → deriva de config.long_form.working_dir/checkpoints.
    store = get_checkpoint_store()
    assert store.base_dir.name == "checkpoints"
    assert "long_form" in str(store.base_dir)
