"""전사 캐시 모듈 단위 테스트.

캐시는 정확성에 영향 없는 최적화다. 키는 원본 오디오 내용 해시이고, 저장/복원은
Segment 필드(None 포함)를 보존하며, 손상/버전불일치 캐시는 조용히 미스 처리한다.
"""

from __future__ import annotations

import pytest

from src import cache
from src.exceptions import CacheError
from src.transcribe import Segment


def test_compute_key_same_content_same_key(tmp_path):
    a = tmp_path / "a.bin"
    b = tmp_path / "b.bin"
    a.write_bytes(b"hello world" * 1000)
    b.write_bytes(b"hello world" * 1000)
    assert cache.compute_key(a) == cache.compute_key(b)


def test_compute_key_different_content_different_key(tmp_path):
    a = tmp_path / "a.bin"
    b = tmp_path / "b.bin"
    a.write_bytes(b"hello")
    b.write_bytes(b"world")
    assert cache.compute_key(a) != cache.compute_key(b)


def test_compute_key_missing_file_raises(tmp_path):
    with pytest.raises(CacheError):
        cache.compute_key(tmp_path / "nope.bin")


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


def test_store_creates_dir(tmp_path):
    target = tmp_path / "transcripts" / "nested"
    cache.store(target, "k", _segments())
    assert (target / "k.json").is_file()


def test_store_no_tmp_leftover(tmp_path):
    cache.store(tmp_path, "k", _segments())
    # 원자적 쓰기: 최종 .json 만 남고 임시 파일 잔여물이 없어야 한다.
    assert [p.name for p in tmp_path.iterdir()] == ["k.json"]


def test_load_missing_key_returns_none(tmp_path):
    assert cache.load(tmp_path, "missing") is None
