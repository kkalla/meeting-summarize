"""전사 결과 캐시: 원본 오디오 SHA-256 키 ↔ list[Segment] JSON.

캐시는 정확성에 영향을 주지 않는 순수 최적화다. 어떤 캐시 실패도 파이프라인을
중단시키지 않는다(최악의 경우 전사를 한 번 더 할 뿐). 순수 함수로 두어 단위 테스트
가능하게 한다.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import time
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from legacy.transcribe import Segment
from src.exceptions import CacheError

if TYPE_CHECKING:
    from src.config import SttConfig

logger = logging.getLogger(__name__)

CACHE_VERSION = 1
# 대용량 오디오 대비 1MB 씩 스트리밍 해싱한다(전체를 메모리에 올리지 않는다).
_HASH_CHUNK_BYTES = 1024 * 1024
_SUFFIX = ".json"


def _stt_fingerprint(stt: SttConfig) -> str:
    """전사 출력을 좌우하는 STT 설정의 짧은 지문(hex 16자)을 반환한다.

    같은 오디오라도 모델/언어/초기 프롬프트가 다르면 전사 결과가 달라지므로, 이 지문을
    캐시 키에 섞어 설정 변경 시 옛 전사가 잘못 재사용되는 것을 막는다. ETA 표시 전용인
    ``rtf_estimate`` 나 ``timeout_sec`` 처럼 출력에 영향 없는 필드는 제외한다.

    ``prompt`` 는 :class:`SttConfig` 가 항상 갖는 필드라 직접 읽는다. getattr 기본값으로
    감싸면 향후 필드가 사라질 때 AttributeError 를 삼켜 전부 ""로 해싱 → 캐시 충돌(낡은
    전사 재사용)을 은폐하므로, 의도적으로 직접 접근해 그런 회귀가 즉시 드러나게 한다.
    """
    fingerprint = {
        "model_path": stt.model_path,
        "language": stt.language,
        "prompt": stt.prompt,
    }
    encoded = json.dumps(fingerprint, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:16]


def compute_key(audio_path: Path, stt: SttConfig) -> str:
    """``<오디오 SHA-256>-<STT 설정 지문>`` 형태의 캐시 키를 반환한다.

    오디오 내용뿐 아니라 전사에 쓰인 모델/언어까지 키에 반영해, 모델·언어를 바꾸면
    같은 오디오라도 캐시가 미스되어 새 설정으로 다시 전사된다.

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
    return f"{digest.hexdigest()}-{_stt_fingerprint(stt)}"


def _cache_path(cache_dir: Path, key: str) -> Path:
    return cache_dir / f"{key}{_SUFFIX}"


def store(cache_dir: Path, key: str, segments: list[Segment], source_name: str = "") -> None:
    """전사 결과를 캐시에 원자적으로 저장한다.

    tmp 파일에 쓰고 같은 디렉토리 내에서 rename 해 부분 쓰인 캐시가 보이지 않게 한다.
    저장 실패는 로그만 남기고 삼킨다(파이프라인을 막지 않는다 — 이번엔 캐시를 못 남길 뿐).

    Args:
        source_name: 원본 파일명(예: ``회의.qta``). 복원에는 쓰지 않고, 사람이 캐시
            파일만 보고 어느 녹음의 전사인지 식별하기 위한 디버깅용 메타데이터다.
    """
    payload = {
        "version": CACHE_VERSION,
        "source_name": source_name,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "segments": [asdict(seg) for seg in segments],
    }
    tmp = cache_dir / f".{key}{_SUFFIX}.tmp"
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        tmp.replace(_cache_path(cache_dir, key))
    except OSError as exc:
        logger.warning("전사 캐시 저장 실패(무시): %s (%s)", key, exc)
        with contextlib.suppress(OSError):
            tmp.unlink(missing_ok=True)


def load(cache_dir: Path, key: str) -> list[Segment] | None:
    """캐시를 조회한다. HIT 면 list[Segment], 그 외(없음/손상/버전불일치)면 None.

    None 은 "캐시 미스"로 해석되어 호출부가 재전사하면 된다(데이터 안전 우선).
    """
    path = _cache_path(cache_dir, key)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("전사 캐시 읽기/파싱 실패 — 미스 처리: %s (%s)", key, exc)
        return None
    if not isinstance(data, dict) or data.get("version") != CACHE_VERSION:
        logger.warning("전사 캐시 버전 불일치/구조 손상 — 미스 처리: %s", key)
        return None
    try:
        return [Segment(**seg) for seg in data["segments"]]
    except (KeyError, TypeError) as exc:
        logger.warning("전사 캐시 스키마 손상 — 미스 처리: %s (%s)", key, exc)
        return None


def purge_expired(cache_dir: Path, ttl_hours: float) -> int:
    """mtime 이 TTL 을 넘긴 캐시 파일(*.json)을 삭제하고 삭제 개수를 반환한다.

    개별 파일 삭제 실패(권한/경합 등)는 건너뛰고 계속한다. 디렉토리가 없으면 0.
    """
    if not cache_dir.is_dir():
        return 0
    cutoff = time.time() - ttl_hours * 3600
    removed = 0
    for path in cache_dir.glob(f"*{_SUFFIX}"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
                removed += 1
        except OSError as exc:
            logger.warning("캐시 파일 삭제 실패(건너뜀): %s (%s)", path.name, exc)
    return removed
