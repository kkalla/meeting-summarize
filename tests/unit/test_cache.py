"""전사 캐시 모듈 단위 테스트.

캐시는 정확성에 영향 없는 최적화다. 키는 원본 오디오 내용 해시이고, 저장/복원은
Segment 필드(None 포함)를 보존하며, 손상/버전불일치 캐시는 조용히 미스 처리한다.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from types import SimpleNamespace

import pytest

from src import cache
from src.exceptions import CacheError
from src.transcribe import Segment


def _stt(model_path: str = "ggml-large-v3-turbo.bin", language: str = "ko"):
    """compute_key 가 보는 최소 STT 설정 더블 (model_path/language 만 읽는다)."""
    return SimpleNamespace(model_path=model_path, language=language)


def test_compute_key_same_content_same_key(tmp_path):
    a = tmp_path / "a.bin"
    b = tmp_path / "b.bin"
    a.write_bytes(b"hello world" * 1000)
    b.write_bytes(b"hello world" * 1000)
    assert cache.compute_key(a, _stt()) == cache.compute_key(b, _stt())


def test_compute_key_different_content_different_key(tmp_path):
    a = tmp_path / "a.bin"
    b = tmp_path / "b.bin"
    a.write_bytes(b"hello")
    b.write_bytes(b"world")
    assert cache.compute_key(a, _stt()) != cache.compute_key(b, _stt())


def test_compute_key_different_model_different_key(tmp_path):
    # 같은 오디오라도 모델이 다르면 키가 달라야 한다(옛 모델 전사 재사용 방지).
    a = tmp_path / "a.bin"
    a.write_bytes(b"hello world" * 1000)
    assert cache.compute_key(a, _stt(model_path="base.bin")) != cache.compute_key(a, _stt(model_path="large.bin"))


def test_compute_key_different_language_different_key(tmp_path):
    # 같은 오디오라도 언어가 다르면 키가 달라야 한다.
    a = tmp_path / "a.bin"
    a.write_bytes(b"hello world" * 1000)
    assert cache.compute_key(a, _stt(language="ko")) != cache.compute_key(a, _stt(language="en"))


def test_compute_key_missing_file_raises(tmp_path):
    with pytest.raises(CacheError):
        cache.compute_key(tmp_path / "nope.bin", _stt())


def _segments():
    return [
        Segment(start=0.0, end=3.2, text="안녕하세요", no_speech_prob=0.01, avg_logprob=-0.3),
        Segment(start=3.2, end=6.0, text="회의 시작합니다", no_speech_prob=None, avg_logprob=None),
    ]


def test_store_load_roundtrip(tmp_path):
    key = "abc123"
    cache.store(tmp_path, key, _segments())
    loaded = cache.load(tmp_path, key)
    assert loaded == _segments()  # frozen dataclass 동등성 — None 필드까지 보존


def test_store_writes_source_name_metadata(tmp_path):
    # source_name 은 복원에는 안 쓰지만, 캐시 파일만 보고 어느 녹음인지 알 수 있게 남긴다.
    cache.store(tmp_path, "k", _segments(), source_name="회의.qta")
    data = json.loads((tmp_path / "k.json").read_text(encoding="utf-8"))
    assert data["source_name"] == "회의.qta"
    # 메타데이터가 있어도 복원은 정상 동작해야 한다.
    assert cache.load(tmp_path, "k") == _segments()


def test_store_creates_dir(tmp_path):
    target = tmp_path / "transcripts" / "nested"
    cache.store(target, "k", _segments())
    assert (target / "k.json").is_file()


def test_store_no_tmp_leftover(tmp_path):
    cache.store(tmp_path, "k", _segments())
    # 원자적 쓰기: 최종 .json 만 남고 임시 파일 잔여물이 없어야 한다.
    assert (tmp_path / "k.json").is_file()
    assert list(tmp_path.glob("*.tmp")) == []


def test_store_cleans_tmp_on_replace_failure(tmp_path, monkeypatch):
    from pathlib import Path as _P

    def _boom(self, target):
        raise OSError("rename 실패")

    monkeypatch.setattr(_P, "replace", _boom)
    cache.store(tmp_path, "k", _segments())  # 예외 전파 없이 로그만
    # 최종 json도 없고, tmp 잔여물도 없어야 한다
    assert list(tmp_path.glob("*.tmp")) == []
    assert not (tmp_path / "k.json").exists()


def test_load_missing_key_returns_none(tmp_path):
    assert cache.load(tmp_path, "missing") is None


def test_load_corrupt_json_returns_none(tmp_path):
    (tmp_path / "k.json").write_text("{not valid json", encoding="utf-8")
    assert cache.load(tmp_path, "k") is None


def test_load_version_mismatch_returns_none(tmp_path):
    payload = {"version": 999, "created_at": "x", "segments": []}
    (tmp_path / "k.json").write_text(json.dumps(payload), encoding="utf-8")
    assert cache.load(tmp_path, "k") is None


def test_load_broken_schema_returns_none(tmp_path):
    # segments 항목에 Segment 가 모르는 필드 → TypeError → None
    payload = {"version": cache.CACHE_VERSION, "segments": [{"bogus": 1}]}
    (tmp_path / "k.json").write_text(json.dumps(payload), encoding="utf-8")
    assert cache.load(tmp_path, "k") is None


def test_purge_expired_removes_only_old(tmp_path):
    fresh = tmp_path / "fresh.json"
    stale = tmp_path / "stale.json"
    fresh.write_text("{}", encoding="utf-8")
    stale.write_text("{}", encoding="utf-8")
    # stale 의 mtime 을 48시간 전으로 되돌린다.
    old = datetime.now().timestamp() - 48 * 3600
    os.utime(stale, (old, old))

    removed = cache.purge_expired(tmp_path, ttl_hours=24)

    assert removed == 1
    assert fresh.is_file()
    assert not stale.is_file()


def test_purge_expired_missing_dir_returns_zero(tmp_path):
    assert cache.purge_expired(tmp_path / "nope", ttl_hours=24) == 0


def test_purge_expired_ignores_non_json(tmp_path):
    other = tmp_path / "keep.txt"
    other.write_text("x", encoding="utf-8")
    old = datetime.now().timestamp() - 48 * 3600
    os.utime(other, (old, old))
    assert cache.purge_expired(tmp_path, ttl_hours=24) == 0
    assert other.is_file()
