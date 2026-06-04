# 전사 결과 캐시 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 전사 성공분을 원본 오디오 SHA-256 키로 디스크에 캐시해, 요약 단계 실패 후 재처리 시 전사를 건너뛴다 (issue #5).

**Architecture:** 캐시 책임을 순수 모듈 `src/cache.py` 에 격리한다. `run_pipeline` 은 전사 직전 캐시를 조회(HIT 시 전사 스킵)하고 MISS 시 전사 후 저장한다. watcher 는 스캔마다 TTL 만료 캐시를 정리한다. 캐시는 정확성에 영향 없는 최적화이므로 캐시 관련 실패는 절대 파이프라인을 중단시키지 않는다(전사로 폴백).

**Tech Stack:** Python 3.11+, dataclasses, hashlib, pytest. 외부 의존성 추가 없음.

**Spec:** `docs/superpowers/specs/2026-06-04-transcript-cache-design.md`

---

## 파일 구조

- Create: `src/cache.py` — 캐시 순수 함수 (compute_key/load/store/purge_expired)
- Create: `tests/unit/test_cache.py` — cache 모듈 단위 테스트
- Create: `tests/unit/test_pipeline.py` — `_transcribe_or_load` 캐시 분기 테스트
- Modify: `src/exceptions.py` — `CacheError` 추가
- Modify: `src/config.py` — `CacheConfig` + `cache` 섹션 파싱
- Modify: `src/pipeline.py` — `_transcribe_or_load` 헬퍼로 전사 블록 교체
- Modify: `src/watcher.py` — `_scan_once` 에 `_purge_cache` 추가
- Modify: `tests/unit/test_config.py` — `cache` 섹션 파싱/검증 테스트
- Modify: `tests/unit/test_watcher.py` — purge 호출/예외격리 테스트
- Modify: `configs/pipeline.yaml` — `cache` 섹션 추가
- Modify: `compose.yaml` — `/data/transcripts` 볼륨 마운트

---

## Task 1: CacheError 예외 추가

**Files:**
- Modify: `src/exceptions.py`
- Test: (기존 import 로 간접 검증, 별도 테스트 불필요)

- [ ] **Step 1: `CacheError` 추가**

`src/exceptions.py` 의 `SummarizationError` 클래스 아래에 추가:

```python
class CacheError(PipelineError):
    """전사 캐시 조회/저장 실패. 치명적이지 않게 다룬다(캐시 없이 전사로 폴백)."""
```

- [ ] **Step 2: import 검증**

Run: `python -c "from src.exceptions import CacheError; print(CacheError.__mro__)"`
Expected: `CacheError -> PipelineError -> Exception` 가 출력됨(PipelineError 상속 확인).

- [ ] **Step 3: Commit**

```bash
git add src/exceptions.py
git commit -m "feat: 전사 캐시용 CacheError 예외 추가"
```

---

## Task 2: cache.compute_key (오디오 내용 SHA-256)

**Files:**
- Create: `src/cache.py`
- Test: `tests/unit/test_cache.py`

- [ ] **Step 1: 실패 테스트 작성**

`tests/unit/test_cache.py` 생성:

```python
"""전사 캐시 모듈 단위 테스트.

캐시는 정확성에 영향 없는 최적화다. 키는 원본 오디오 내용 해시이고, 저장/복원은
Segment 필드(None 포함)를 보존하며, 손상/버전불일치 캐시는 조용히 미스 처리한다.
"""

from __future__ import annotations

import json
import os

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
```

- [ ] **Step 2: 실패 확인**

Run: `python -m pytest tests/unit/test_cache.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.cache'`

- [ ] **Step 3: 최소 구현**

`src/cache.py` 생성:

```python
"""전사 결과 캐시: 원본 오디오 SHA-256 키 ↔ list[Segment] JSON.

캐시는 정확성에 영향을 주지 않는 순수 최적화다. 어떤 캐시 실패도 파이프라인을
중단시키지 않는다(최악의 경우 전사를 한 번 더 할 뿐). 순수 함수로 두어 단위 테스트
가능하게 한다.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from src.exceptions import CacheError
from src.transcribe import Segment

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
```

- [ ] **Step 4: 통과 확인**

Run: `python -m pytest tests/unit/test_cache.py -q`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add src/cache.py tests/unit/test_cache.py
git commit -m "feat: 전사 캐시 compute_key (오디오 SHA-256)"
```

---

## Task 3: cache.store / cache.load (round-trip)

**Files:**
- Modify: `src/cache.py`
- Test: `tests/unit/test_cache.py`

- [ ] **Step 1: 실패 테스트 작성**

`tests/unit/test_cache.py` 끝에 추가:

```python
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
```

- [ ] **Step 2: 실패 확인**

Run: `python -m pytest tests/unit/test_cache.py -q`
Expected: FAIL — `AttributeError: module 'src.cache' has no attribute 'store'`

- [ ] **Step 3: 최소 구현**

`src/cache.py` 에 추가:

```python
def _cache_path(cache_dir: Path, key: str) -> Path:
    return cache_dir / f"{key}{_SUFFIX}"


def store(cache_dir: Path, key: str, segments: list[Segment]) -> None:
    """전사 결과를 캐시에 원자적으로 저장한다.

    tmp 파일에 쓰고 같은 디렉토리 내에서 rename 해 부분 쓰인 캐시가 보이지 않게 한다.
    저장 실패는 로그만 남기고 삼킨다(파이프라인을 막지 않는다 — 이번엔 캐시를 못 남길 뿐).
    """
    payload = {
        "version": CACHE_VERSION,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "segments": [asdict(seg) for seg in segments],
    }
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        tmp = cache_dir / f".{key}{_SUFFIX}.tmp"
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        tmp.replace(_cache_path(cache_dir, key))
    except OSError as exc:
        logger.warning("전사 캐시 저장 실패(무시): %s (%s)", key, exc)


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
```

- [ ] **Step 4: 통과 확인**

Run: `python -m pytest tests/unit/test_cache.py -q`
Expected: PASS (7 passed)

- [ ] **Step 5: Commit**

```bash
git add src/cache.py tests/unit/test_cache.py
git commit -m "feat: 전사 캐시 store/load 원자적 round-trip"
```

---

## Task 4: cache.load 손상/버전 불일치 처리

**Files:**
- Test: `tests/unit/test_cache.py` (구현은 Task 3 에서 이미 완료 — 회귀 가드 테스트 추가)

- [ ] **Step 1: 실패할 수 있는 회귀 테스트 작성**

`tests/unit/test_cache.py` 끝에 추가:

```python
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
```

- [ ] **Step 2: 통과 확인 (구현은 이미 존재)**

Run: `python -m pytest tests/unit/test_cache.py -q`
Expected: PASS (10 passed) — Task 3 의 `load` 가 세 경로를 모두 처리.

- [ ] **Step 3: Commit**

```bash
git add tests/unit/test_cache.py
git commit -m "test: 전사 캐시 손상/버전불일치 미스 처리 가드"
```

---

## Task 5: cache.purge_expired (TTL 정리)

**Files:**
- Modify: `src/cache.py`
- Test: `tests/unit/test_cache.py`

- [ ] **Step 1: 실패 테스트 작성**

`tests/unit/test_cache.py` 끝에 추가:

```python
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
```

테스트 상단 import 에 `from datetime import datetime` 추가 (필요 시 파일 상단 import 블록에 반영).

- [ ] **Step 2: 실패 확인**

Run: `python -m pytest tests/unit/test_cache.py -q`
Expected: FAIL — `AttributeError: module 'src.cache' has no attribute 'purge_expired'`

- [ ] **Step 3: 최소 구현**

`src/cache.py` 에 추가:

```python
import time


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
```

`import time` 은 파일 상단 import 블록(`import logging` 옆)으로 정리해도 좋다.

- [ ] **Step 4: 통과 확인**

Run: `python -m pytest tests/unit/test_cache.py -q`
Expected: PASS (13 passed)

- [ ] **Step 5: Commit**

```bash
git add src/cache.py tests/unit/test_cache.py
git commit -m "feat: 전사 캐시 purge_expired (TTL 정리)"
```

---

## Task 6: CacheConfig + config 파싱

**Files:**
- Modify: `src/config.py`
- Test: `tests/unit/test_config.py`

- [ ] **Step 1: 실패 테스트 작성**

`tests/unit/test_config.py` 끝에 추가:

```python
# --- CacheConfig ----------------------------------------------------------

from src.config import CacheConfig  # noqa: E402  (테스트 가독성 위해 지역 import)


def test_cache_config_rejects_non_positive_ttl_when_enabled():
    with pytest.raises(DependencyError):
        CacheConfig(transcripts_dir=Path("/data/transcripts"), ttl_hours=0, enabled=True)


def test_cache_config_allows_any_ttl_when_disabled():
    # disabled 면 ttl 은 쓰이지 않으므로 검증하지 않는다.
    cfg = CacheConfig(transcripts_dir=Path("/data/transcripts"), ttl_hours=0, enabled=False)
    assert cfg.enabled is False


def test_build_config_parses_cache_section():
    raw = _raw()
    raw["cache"] = {"transcripts_dir": "/data/transcripts", "ttl_hours": 168, "enabled": True}
    cfg = _build_config(raw, api_key="k")
    assert cfg.cache.enabled is True
    assert cfg.cache.transcripts_dir == Path("/data/transcripts")
    assert cfg.cache.ttl_hours == 168.0


def test_build_config_missing_cache_section_defaults_disabled():
    raw = _raw()  # cache 키 없음
    cfg = _build_config(raw, api_key="k")
    assert cfg.cache.enabled is False
```

파일 상단 import 에 `from pathlib import Path` 가 없으면 추가한다.

- [ ] **Step 2: 실패 확인**

Run: `python -m pytest tests/unit/test_config.py -q`
Expected: FAIL — `ImportError: cannot import name 'CacheConfig'`

- [ ] **Step 3: 최소 구현 — CacheConfig dataclass**

`src/config.py` 의 `WatcherConfig` 정의 위(또는 아래)에 추가:

```python
@dataclass(frozen=True)
class CacheConfig:
    """전사 결과 캐시 설정."""

    transcripts_dir: Path
    ttl_hours: float
    enabled: bool

    def __post_init__(self) -> None:
        # disabled 면 ttl 은 사용되지 않으므로 검증하지 않는다. enabled 일 때 ttl<=0 이면
        # 모든 캐시가 즉시 만료되어 캐시가 무력화되므로 로딩 시점에 막는다.
        if self.enabled and self.ttl_hours <= 0:
            raise DependencyError(f"cache.ttl_hours 는 양수여야 합니다: {self.ttl_hours}")
```

- [ ] **Step 4: 최소 구현 — PipelineConfig 필드 + 파싱**

`src/config.py` 의 `PipelineConfig` 에 `cache` 필드 추가:

```python
@dataclass(frozen=True)
class PipelineConfig:
    audio: AudioConfig
    stt: SttConfig
    chunking: ChunkingConfig
    summarize: SummarizeConfig
    watcher: WatcherConfig
    cache: CacheConfig
    api_key: str
```

`_build_config` 의 `return PipelineConfig(...)` 에 `cache=_build_cache_config(raw.get("cache")),` 를 `watcher=...` 다음 줄에 추가하고, 파일에 헬퍼 추가:

```python
# config.py 상단 기본값 상수
DEFAULT_TRANSCRIPTS_DIR = "data/transcripts"
DEFAULT_CACHE_TTL_HOURS = 168.0


def _build_cache_config(cache_raw: dict | None) -> CacheConfig:
    """cache 설정 dict 를 :class:`CacheConfig` 로 변환한다.

    섹션이 없으면 비활성(enabled=False) 기본값으로 둬, cache 섹션이 없는 기존 설정
    파일도 캐시 없이(현행 동작) 그대로 동작하게 한다.
    """
    if cache_raw is None:
        return CacheConfig(
            transcripts_dir=_resolve_dir(DEFAULT_TRANSCRIPTS_DIR),
            ttl_hours=DEFAULT_CACHE_TTL_HOURS,
            enabled=False,
        )
    return CacheConfig(
        transcripts_dir=_resolve_dir(str(cache_raw["transcripts_dir"])),
        ttl_hours=float(cache_raw["ttl_hours"]),
        enabled=bool(cache_raw["enabled"]),
    )
```

- [ ] **Step 5: 통과 확인**

Run: `python -m pytest tests/unit/test_config.py -q`
Expected: PASS (기존 15 + 신규 4 = 19 passed)

- [ ] **Step 6: Commit**

```bash
git add src/config.py tests/unit/test_config.py
git commit -m "feat: CacheConfig 및 cache 섹션 파싱(미존재 시 비활성)"
```

---

## Task 7: pipeline._transcribe_or_load 통합

**Files:**
- Modify: `src/pipeline.py`
- Test: `tests/unit/test_pipeline.py`

- [ ] **Step 1: 실패 테스트 작성**

`tests/unit/test_pipeline.py` 생성:

```python
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
```

- [ ] **Step 2: 실패 확인**

Run: `python -m pytest tests/unit/test_pipeline.py -q`
Expected: FAIL — `AttributeError: module 'src.pipeline' has no attribute 'cache'` 또는 `_transcribe_or_load` 미정의

- [ ] **Step 3: 최소 구현**

`src/pipeline.py` 상단 import 에 추가:

```python
from src import cache
from src.exceptions import CacheError
```

`run_pipeline` 의 `with tempfile.TemporaryDirectory() as tmp:` 블록에서 전사 호출을 교체:

```python
    with tempfile.TemporaryDirectory() as tmp:
        wav_path = Path(tmp) / f"{input_path.stem}{WAV_SUFFIX}"
        convert_to_wav(input_path, wav_path, config.audio)

        logger.info("전사 시작")
        segments = _transcribe_or_load(input_path, wav_path, config)
```

(기존 `segments = transcribe(wav_path, config.stt)` 한 줄을 위 헬퍼 호출로 바꾼다.)

파일에 헬퍼 추가:

```python
def _transcribe_or_load(input_path: Path, wav_path: Path, config: PipelineConfig) -> list[Segment]:
    """전사 캐시를 우선 조회하고, 없으면 전사 후 저장한다.

    캐시가 비활성이거나 키 계산이 실패하면 캐시 없이 전사로 폴백한다(캐시는 정확성에
    영향 없는 최적화 — 어떤 캐시 실패도 전사 자체를 막지 않는다).
    """
    cache_cfg = config.cache
    if not cache_cfg.enabled:
        return transcribe(wav_path, config.stt)
    try:
        key = cache.compute_key(input_path)
    except CacheError as exc:
        logger.warning("캐시 키 계산 실패 — 캐시 없이 전사: %s", exc)
        return transcribe(wav_path, config.stt)

    cached = cache.load(cache_cfg.transcripts_dir, key)
    if cached is not None:
        logger.info("전사 캐시 HIT — 전사 스킵 (세그먼트 %d개)", len(cached))
        return cached

    segments = transcribe(wav_path, config.stt)
    cache.store(cache_cfg.transcripts_dir, key, segments)
    return segments
```

- [ ] **Step 4: 통과 확인**

Run: `python -m pytest tests/unit/test_pipeline.py -q`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add src/pipeline.py tests/unit/test_pipeline.py
git commit -m "feat: run_pipeline 전사 캐시 분기(_transcribe_or_load)"
```

---

## Task 8: watcher._purge_cache (TTL 정리 훅)

**Files:**
- Modify: `src/watcher.py`
- Test: `tests/unit/test_watcher.py`

- [ ] **Step 1: 기존 test_watcher 의 config 구성 방식 확인**

Run: `grep -n "PipelineConfig\|CacheConfig\|_config\|cache" tests/unit/test_watcher.py | head -30`
Expected: 테스트가 `PipelineConfig`/`WatcherConfig` 를 어떻게 조립하는지 파악. `PipelineConfig` 를 직접 만든다면 Task 6 에서 추가된 `cache` 필드를 채워야 한다(없으면 `TypeError`). 픽스처/헬퍼에 `cache=CacheConfig(transcripts_dir=tmp, ttl_hours=168, enabled=...)` 를 추가한다.

- [ ] **Step 2: 실패 테스트 작성**

`tests/unit/test_watcher.py` 에 추가(기존 watcher 인스턴스 생성 헬퍼/픽스처를 재사용. 아래는 패턴 예시 — 실제 픽스처 이름에 맞춰 조정):

```python
def test_scan_once_purges_cache(monkeypatch, make_watcher):
    # make_watcher: 기존 테스트의 watcher 생성 헬퍼(cache.enabled=True 로 구성).
    watcher = make_watcher(cache_enabled=True)
    calls = {"n": 0}
    monkeypatch.setattr("src.watcher.purge_expired", lambda d, ttl: calls.__setitem__("n", calls["n"] + 1) or 0)
    monkeypatch.setattr(watcher, "_list_candidates", lambda: [])
    watcher._scan_once()
    assert calls["n"] == 1


def test_purge_cache_swallows_errors(monkeypatch, make_watcher):
    watcher = make_watcher(cache_enabled=True)

    def _boom(d, ttl):
        raise OSError("디스크 오류")

    monkeypatch.setattr("src.watcher.purge_expired", _boom)
    # 예외가 새지 않아야 한다(데몬 생존 우선).
    watcher._purge_cache()


def test_purge_cache_skipped_when_disabled(monkeypatch, make_watcher):
    watcher = make_watcher(cache_enabled=False)

    def _fail(d, ttl):
        raise AssertionError("disabled 인데 purge_expired 호출됨")

    monkeypatch.setattr("src.watcher.purge_expired", _fail)
    watcher._purge_cache()
```

> 주의: 기존 `tests/unit/test_watcher.py` 가 `make_watcher` 같은 헬퍼를 쓰지 않으면, 그 파일의 기존 watcher 생성 패턴(직접 `FolderWatcher(config, path)` 생성 등)을 그대로 따른다. 핵심은 `config.cache.enabled` 와 `config.cache.transcripts_dir`/`ttl_hours` 가 채워진 `PipelineConfig` 를 넘기는 것.

- [ ] **Step 3: 실패 확인**

Run: `python -m pytest tests/unit/test_watcher.py -q`
Expected: FAIL — `_purge_cache` 또는 `purge_expired` import 미존재

- [ ] **Step 4: 최소 구현**

`src/watcher.py` 상단 import 에 추가:

```python
from src.cache import purge_expired
```

`_scan_once` 시작부에 정리 호출 추가:

```python
    def _scan_once(self) -> None:
        """inbox 를 한 번 스캔해 안정화된 파일을 순차 처리한다."""
        self._purge_cache()
        files = self._list_candidates()
        ...
```

클래스에 메서드 추가:

```python
    def _purge_cache(self) -> None:
        """TTL 만료된 전사 캐시를 정리한다. 어떤 실패도 스캔 루프를 죽이지 않는다.

        캐시 정리는 부가 작업이므로, _scan_once 의 OSError 격리와 동일하게 예외를
        삼키고 로그만 남긴다(데몬 생존 우선).
        """
        cache_cfg = self._config.cache
        if not cache_cfg.enabled:
            return
        try:
            removed = purge_expired(cache_cfg.transcripts_dir, cache_cfg.ttl_hours)
            if removed:
                logger.info("만료 전사 캐시 정리: %d개 삭제", removed)
        except Exception as exc:  # noqa: BLE001 - 데몬 생존 우선: 정리 실패가 루프를 죽이면 안 됨
            logger.warning("전사 캐시 정리 실패(무시): %s", exc)
```

- [ ] **Step 5: 통과 확인**

Run: `python -m pytest tests/unit/test_watcher.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/watcher.py tests/unit/test_watcher.py
git commit -m "feat: watcher 스캔마다 TTL 만료 전사 캐시 정리"
```

---

## Task 9: 설정/컴포즈 반영 + 전체 검증

**Files:**
- Modify: `configs/pipeline.yaml`
- Modify: `compose.yaml`

- [ ] **Step 1: configs/pipeline.yaml 에 cache 섹션 추가**

`summarize:` 섹션 다음(또는 `watcher:` 근처)에 추가:

```yaml
# 전사 결과 캐시. 전사 성공분을 원본 오디오 SHA-256 키로 보관해, 요약 등 후속 단계
# 실패 후 재처리 시 전사(가장 비싼 단계)를 건너뛴다. (issue #5)
cache:
  # 캐시 파일(<sha256>.json) 저장 디렉토리. 절대경로는 그대로, 상대경로는 루트 기준.
  transcripts_dir: "/data/transcripts"
  # 이 시간이 지난 캐시는 watcher 스캔 시 삭제. 168h = 7일.
  ttl_hours: 168
  enabled: true
```

- [ ] **Step 2: compose.yaml 에 transcripts 볼륨 마운트 추가**

Run: `grep -n "data\|volumes\|/data/" compose.yaml`
Expected: 기존 `/data/inbox` 등 마운트 패턴 확인 후, 동일 호스트 데이터 루트 하위에
`/data/transcripts` 가 포함되도록 한다. 기존이 `./data:/data` 형태의 단일 마운트면
`transcripts/` 는 자동 포함되므로 **추가 마운트가 불필요**할 수 있다 — 이 경우 변경 없이
주석으로만 명시한다. 개별 디렉토리 마운트 방식이면 다음을 추가:

```yaml
      - ./data/transcripts:/data/transcripts
```

(실제 compose.yaml 의 마운트 스타일에 맞춰 한쪽만 적용한다.)

- [ ] **Step 3: 전체 테스트 + 포맷 검증**

Run: `python -m pytest -q`
Expected: PASS (기존 70 + 신규 cache 13 + pipeline 4 + config 4 + watcher 3 ≈ 94 passed)

Run: `python -m black --check src tests && python -m isort --check src tests`
Expected: 포맷 통과(실패 시 `black src tests && isort src tests` 후 재커밋).

- [ ] **Step 4: 수동 스모크 — 캐시 동작 확인**

Run:
```bash
python - <<'PY'
import tempfile, pathlib
from src import cache
from src.transcribe import Segment
d = pathlib.Path(tempfile.mkdtemp())
segs = [Segment(0.0, 1.0, "hi", 0.0, -0.1)]
cache.store(d, "k", segs)
assert cache.load(d, "k") == segs
print("캐시 round-trip OK:", list(d.iterdir()))
PY
```
Expected: `캐시 round-trip OK: [PosixPath('.../k.json')]`

- [ ] **Step 5: Commit**

```bash
git add configs/pipeline.yaml compose.yaml
git commit -m "chore: pipeline.yaml cache 섹션 및 transcripts 볼륨 추가"
```

---

## 완료 기준

- [ ] 전체 테스트 통과 (≈94 passed)
- [ ] `enabled: false` 또는 cache 섹션 누락 시 기존 동작과 동일(항상 전사)
- [ ] 캐시 HIT 시 transcribe 미호출(로그 "전사 캐시 HIT")
- [ ] 요약 실패 후 같은 파일 재처리 시 전사 스킵(같은 SHA-256 → HIT)
- [ ] 모든 캐시 실패 경로가 파이프라인/데몬을 중단시키지 않음
- [ ] black/isort 포맷 통과

## 후속 (이번 범위 밖)

- 캐시 디스크 용량 상한(개수/바이트 기반 LRU). 현재는 TTL 만으로 관리.
- 요약 결과 캐싱.
