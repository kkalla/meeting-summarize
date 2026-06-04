# 전사 결과 캐시 설계 (issue #5)

- 날짜: 2026-06-04
- 대상 issue: [#5](https://github.com/kkalla/meeting-summarize/issues/5) — "Failed 케이스에서 전사를 계속 수행"
- 브랜치: `feat/transcript-cache`

## 1. 문제

`run_pipeline` 은 변환 → 전사 → 청킹 → 요약 → 리포트를 한 함수에서 순차 수행한다.
전사 이후 단계(요약 등)에서 실패하면 watcher 가 원본을 `failed/` 로 옮기는데, 이때
전사 결과(`list[Segment]`)는 메모리에만 있다가 버려진다.

재처리하려고 `failed/` 의 파일을 `inbox/` 로 되돌리면 watcher 가 **전사를 처음부터
다시** 수행한다. 전사는 회의 길이에 비례해 수 분~십수 분이 걸리는 가장 비싼 단계라,
전사가 멀쩡히 성공했음에도 요약만 실패한 케이스에서 전사를 반복하는 것은 명백한
리소스 낭비다. (실측: `선릉로.qta` 전사 약 8분 30초 → 요약 단계 모델 폴백 전부 실패.)

## 2. 목표 / 비목표

**목표**
- 전사 성공분을 디스크에 보관하고, 동일 입력 재처리 시 전사를 건너뛴다.
- 캐시는 정확성에 영향을 주지 않는 순수 최적화다. 캐시 관련 실패가 파이프라인을
  중단시키면 안 된다(최악의 경우 전사를 한 번 더 할 뿐).

**비목표**
- 파이프라인을 다단계 잡 스케줄러로 재설계하지 않는다(YAGNI).
- 요약 결과 캐싱은 범위 밖.

## 3. 핵심 결정 (확정)

| 항목 | 결정 | 근거 |
|------|------|------|
| 재처리 트리거 | 자동 재처리(캐시 우선) | `failed/→inbox/` 되돌리기만으로 전사 스킵·요약부터 재개. 운영자 개입 최소화 |
| 캐시 키 | 원본 오디오 내용 SHA-256 | 파일명 충돌/내용 변경에 안전. 같은 이름 다른 회의 → 미스(올바름), 재업로드 → 자동 재전사 |
| 캐시 수명 | TTL 기반 보관 | 성공/실패 무관 보관. 성공분도 재요약 가능. watcher 가 스캔마다 만료분 정리 |
| 책임 격리 | 별도 `src/cache.py` 순수 모듈 | transcribe/summarize 는 캐시를 모름. 단위 테스트 용이, 기존 코드 변경 최소 |

## 4. 데이터 흐름

```
run_pipeline(input_path, output_path, config_path)
  1. load_config                         # config.cache 포함
  2. with TemporaryDirectory:
       convert_to_wav(input → WAV)
       segments = _transcribe_or_load(input_path, wav_path, config)
         ├─ cache.enabled == False → transcribe(wav)                 # 기존 동작
         └─ enabled:
              key = cache.compute_key(input_path)   # 원본 오디오 SHA-256
              cached = cache.load(cache_dir, key)
              ├─ HIT  → segments = cached  (전사 스킵, "캐시 HIT" 로그)
              └─ MISS → segments = transcribe(wav)
                        cache.store(cache_dir, key, segments)   # 원자적 쓰기
  3. chunk_segments
  4. summarize_meeting        # 실패해도 캐시는 이미 디스크에 남음
  5. render_report → 성공
```

**재처리 경로**: 요약 실패 → watcher 가 원본을 `failed/` 로 이동 → 운영자가
`failed/회의.qta` 를 `inbox/` 로 되돌림 → 같은 내용 → **같은 SHA-256** → 캐시 HIT →
전사 스킵, 요약부터 재개.

**TTL 정리**: watcher `_scan_once` 시작부에서 `cache.purge_expired(cache_dir, ttl)` 호출.
mtime 이 TTL 초과한 `*.json` 삭제. 정리 실패가 데몬 루프를 죽이지 않도록 예외 격리.

**해시 대상은 변환 전 원본 입력 파일**이다. WAV 는 매 실행마다 새로 변환되므로 키로
쓸 수 없다. 대용량 대비 1MB 청크 단위로 읽어 해싱한다.

## 5. 컴포넌트

### 5.1 `src/cache.py` (신규, 순수 함수)

```python
CACHE_VERSION = 1
_HASH_CHUNK_BYTES = 1024 * 1024   # 1MB

def compute_key(audio_path: Path) -> str:
    """원본 오디오 내용의 SHA-256 hex. 파일을 못 읽으면 CacheError."""

def load(cache_dir: Path, key: str) -> list[Segment] | None:
    """HIT 면 list[Segment]. MISS/손상 JSON/버전 불일치/스키마 깨짐 → None(재전사)."""

def store(cache_dir: Path, key: str, segments: list[Segment]) -> None:
    """tmp 파일에 쓰고 원자적 rename. 디렉토리 자동 생성. 저장 실패는 로그만."""

def purge_expired(cache_dir: Path, ttl_hours: float) -> int:
    """mtime 이 TTL 초과한 *.json 삭제. 삭제 개수 반환. 개별 파일 실패는 건너뜀."""
```

**캐시 파일** `<transcripts_dir>/<sha256>.json`:
```json
{
  "version": 1,
  "source_name": "회의.qta",
  "created_at": "2026-06-04T07:02:15",
  "segments": [
    {"start": 0.0, "end": 3.2, "text": "...", "no_speech_prob": 0.01, "avg_logprob": -0.3}
  ]
}
```
`source_name`/`created_at` 은 사람이 디버깅할 때 보는 메타데이터다. 복원에는 `segments`
만 사용한다. `Segment` 의 5개 필드(start/end/text/no_speech_prob/avg_logprob)를 그대로
직렬화하며, `None` 값(신뢰도 데이터 없는 빌드)도 보존한다.

### 5.2 `src/config.py` — `cache` 섹션 추가

```yaml
cache:
  transcripts_dir: "/data/transcripts"   # 절대경로는 그대로, 상대경로는 루트 기준
  ttl_hours: 168                          # 7일
  enabled: true
```

```python
@dataclass(frozen=True)
class CacheConfig:
    transcripts_dir: Path
    ttl_hours: float
    enabled: bool

    def __post_init__(self) -> None:
        # enabled 일 때만 의미. ttl<=0 이면 즉시 만료라 캐시가 무력화되므로 막는다.
        if self.enabled and self.ttl_hours <= 0:
            raise DependencyError(f"cache.ttl_hours 는 양수여야 합니다: {self.ttl_hours}")
```

- `transcripts_dir` 는 `_resolve_dir` 로 해석(watcher 디렉토리와 동일 규칙: 절대경로는
  그대로, 상대경로는 프로젝트 루트 기준).
- `PipelineConfig` 에 `cache: CacheConfig` 필드 추가.
- **하위호환**: YAML 에 `cache` 섹션이 없으면 `enabled: false` 기본값으로 처리해 기존
  설정 파일도 그대로 동작하게 한다(캐시 없이 항상 전사 = 현행 동작).

### 5.3 `src/pipeline.py` — 전사 블록만 교체

```python
def run_pipeline(...):
    config = load_config(config_path)
    ...
    with tempfile.TemporaryDirectory() as tmp:
        wav_path = ...
        convert_to_wav(input_path, wav_path, config.audio)
        segments = _transcribe_or_load(input_path, wav_path, config)  # 신규 헬퍼

def _transcribe_or_load(input_path, wav_path, config) -> list[Segment]:
    """cache.enabled 면 키 계산→load→HIT 반환 / MISS 시 transcribe 후 store.
    disabled 면 transcribe 만. compute_key 실패 시 캐시 없이 transcribe 로 폴백."""
```

`_transcribe_or_load` 는 WAV 가 tmp 에서 사라지기 전에 호출되도록 `with` 블록 안에 둔다.

### 5.4 `src/watcher.py` — `_scan_once` 에 정리 1줄

```python
def _scan_once(self):
    self._purge_cache()           # TTL 만료 캐시 정리(예외 격리)
    files = self._list_candidates()
    ...

def _purge_cache(self):
    """cache.enabled 일 때만 purge_expired 호출. 어떤 예외도 로그만 남기고 삼킨다
    (데몬 생존 우선 — _scan_once 의 OSError 처리 패턴과 동일)."""
```

### 5.5 `src/exceptions.py` — `CacheError`

```python
class CacheError(PipelineError):
    """캐시 조회/저장 실패. 치명적이지 않게 다룬다(전사로 폴백)."""
```

## 6. 에러 처리 원칙

- 캐시는 **최적화일 뿐 정확성에 영향 없음** → 캐시 관련 실패는 절대 파이프라인을 죽이지
  않는다.
  - `compute_key` 실패 → 캐시 없이 전사로 폴백.
  - `store` 실패 → 로그만, 파이프라인 계속(이번엔 캐시 못 남길 뿐).
  - `load` 가 손상/버전불일치 만남 → `None` 반환(조용히 재전사).
  - `purge_expired` 실패 → watcher 로그만, 루프 계속.
- 캐시 HIT 데이터는 저장 시점에 이미 `apply_confidence_gate` 를 통과한 검증된
  segments 이므로 재검증하지 않는다.

## 7. 테스트 (TDD, pytest)

**`tests/unit/test_cache.py` (신규)**
- `compute_key`: 같은 내용 동일 키 / 다른 내용 다른 키 / 없는 파일 → `CacheError`
- round-trip: `store` → `load` 가 동일 segments (float·`None` 필드 보존)
- `load`: 손상 JSON / `version` 불일치 / 없는 키 → `None`
- `store`: 원자적 쓰기(tmp 잔여물 없음), 디렉토리 자동 생성
- `purge_expired`: TTL 초과분만 삭제 / 개수 반환 / 손상·비대상 파일 건너뜀

**`tests/unit/test_config.py` (보강)**
- `cache` 섹션 파싱 / `ttl_hours<=0` 거부 / `enabled:false` / 섹션 누락 시 기본 disabled

**`tests/unit/test_pipeline.py` (보강)**
- 캐시 HIT 시 `transcribe` 미호출(mock) / MISS 시 `store` 호출 / disabled 시 항상 transcribe
- `compute_key` 실패해도 전사로 폴백하고 성공

**`tests/unit/test_watcher.py` (보강)**
- `_scan_once` 가 `_purge_cache` 호출 / purge 예외가 스캔 루프를 죽이지 않음

## 8. 부수 변경

- `configs/pipeline.yaml`: `cache` 섹션 추가(`transcripts_dir: /data/transcripts`,
  `ttl_hours: 168`, `enabled: true`).
- `compose.yaml`: `transcripts` 볼륨/마운트 추가(`/data/transcripts` 영속화).
- `.env.example` / README: 캐시 동작 한 줄 문서화(필요 시).

## 9. 미해결 / 후속

- 캐시 디스크 용량 상한(개수/바이트)은 이번 범위 밖. TTL 만으로 충분하다고 판단.
  추후 필요하면 `purge_expired` 에 LRU/용량 기반 정리를 덧붙인다.
