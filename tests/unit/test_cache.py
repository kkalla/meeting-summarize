"""전사 캐시 모듈 단위 테스트.

캐시는 정확성에 영향 없는 최적화다. 키는 원본 오디오 내용 해시이고, 저장/복원은
Segment 필드(None 포함)를 보존하며, 손상/버전불일치 캐시는 조용히 미스 처리한다.
"""

from __future__ import annotations

import pytest

from src import cache
from src.exceptions import CacheError


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
