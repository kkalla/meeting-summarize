"""run_pipeline 전사 캐시 분기(_transcribe_or_load) 단위 테스트.

전사는 가장 비싼 단계이므로 캐시 HIT 시 transcribe 가 호출되지 않아야 하고,
MISS 시 전사 결과가 store 되어야 하며, disabled/키계산실패 시 항상 전사로 폴백한다.
"""

from __future__ import annotations

from pathlib import Path

from src import pipeline
from src.config import CacheConfig
from src.transcribe import Segment


def _seg():
    return [Segment(start=0.0, end=1.0, text="x", no_speech_prob=0.0, avg_logprob=-0.1)]


class _Cfg:
    """_transcribe_or_load 가 보는 최소 config 더블 (stt/cache 만 사용)."""

    def __init__(self, cache):
        self.stt = object()
        self.cache = cache


def test_cache_hit_skips_transcribe(monkeypatch, tmp_path):
    cfg = _Cfg(CacheConfig(transcripts_dir=tmp_path, ttl_hours=168, enabled=True))
    monkeypatch.setattr(pipeline.cache, "compute_key", lambda p: "key1")
    monkeypatch.setattr(pipeline.cache, "load", lambda d, k: _seg())
    called = {"transcribe": False}

    def _boom(*a, **k):
        called["transcribe"] = True
        raise AssertionError("HIT 인데 transcribe 가 호출됨")

    monkeypatch.setattr(pipeline, "transcribe", _boom)

    out = pipeline._transcribe_or_load(Path("in.qta"), Path("in.wav"), cfg)
    assert out == _seg()
    assert called["transcribe"] is False


def test_cache_miss_transcribes_and_stores(monkeypatch, tmp_path):
    cfg = _Cfg(CacheConfig(transcripts_dir=tmp_path, ttl_hours=168, enabled=True))
    monkeypatch.setattr(pipeline.cache, "compute_key", lambda p: "key1")
    monkeypatch.setattr(pipeline.cache, "load", lambda d, k: None)
    monkeypatch.setattr(pipeline, "transcribe", lambda wav, stt: _seg())
    stored = {}
    monkeypatch.setattr(pipeline.cache, "store", lambda d, k, s: stored.update({"k": k, "s": s}))

    out = pipeline._transcribe_or_load(Path("in.qta"), Path("in.wav"), cfg)
    assert out == _seg()
    assert stored["k"] == "key1"
    assert stored["s"] == _seg()


def test_cache_disabled_always_transcribes(monkeypatch, tmp_path):
    cfg = _Cfg(CacheConfig(transcripts_dir=tmp_path, ttl_hours=168, enabled=False))

    def _no_key(p):
        raise AssertionError("disabled 인데 compute_key 가 호출됨")

    monkeypatch.setattr(pipeline.cache, "compute_key", _no_key)
    monkeypatch.setattr(pipeline, "transcribe", lambda wav, stt: _seg())

    out = pipeline._transcribe_or_load(Path("in.qta"), Path("in.wav"), cfg)
    assert out == _seg()


def test_compute_key_failure_falls_back_to_transcribe(monkeypatch, tmp_path):
    from src.exceptions import CacheError

    cfg = _Cfg(CacheConfig(transcripts_dir=tmp_path, ttl_hours=168, enabled=True))

    def _raise(p):
        raise CacheError("못 읽음")

    monkeypatch.setattr(pipeline.cache, "compute_key", _raise)
    monkeypatch.setattr(pipeline, "transcribe", lambda wav, stt: _seg())

    out = pipeline._transcribe_or_load(Path("in.qta"), Path("in.wav"), cfg)
    assert out == _seg()
