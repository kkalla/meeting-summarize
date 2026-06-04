"""전사 결과 캐시: 원본 오디오 SHA-256 키 ↔ list[Segment] JSON.

캐시는 정확성에 영향을 주지 않는 순수 최적화다. 어떤 캐시 실패도 파이프라인을
중단시키지 않는다(최악의 경우 전사를 한 번 더 할 뿐). 순수 함수로 두어 단위 테스트
가능하게 한다.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

from src.exceptions import CacheError

logger = logging.getLogger(__name__)

CACHE_VERSION = 1
# 대용량 오디오 대비 1MB 씩 스트리밍 해싱한다(전체를 메모리에 올리지 않는다).
_HASH_CHUNK_BYTES = 1024 * 1024
_SUFFIX = ".json"


def compute_key(audio_path: Path) -> str:
    """원본 오디오 내용의 SHA-256 hex 를 반환한다.

    Raises:
        CacheError: 파일을 읽을 수 없을 때(캐시 비활성과 동일하게 전사로 폴백시킨다).
    """
    digest = hashlib.sha256()
    try:
        with audio_path.open("rb") as fp:
            for block in iter(lambda: fp.read(_HASH_CHUNK_BYTES), b""):
                digest.update(block)
    except OSError as exc:
        raise CacheError(f"캐시 키 계산용 파일을 읽을 수 없습니다: {audio_path} ({exc})") from exc
    return digest.hexdigest()
