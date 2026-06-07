# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

회의 녹음 파일 하나를 받아 **로컬 STT(whisper.cpp) + OpenRouter 무료티어 LLM**으로 구조화된 Markdown 요약을 뽑는 개인용 단일 CLI 파이프라인. 서버/REST/웹UI/DB/화자분리/실시간 스트리밍은 의도적으로 범위 밖(KISS).

## 명령어

`uv` 기반. 패키지는 `src` 가 곧 설치 대상(`[tool.hatch.build.targets.wheel] packages = ["src"]`)이라 `from src.xxx import ...` 로 import 하고, pytest 는 `pythonpath = ["."]` 로 루트에서 import 를 해석한다.

```bash
uv sync --dev                                   # 의존성 설치
uv run python scripts/run_pipeline.py 회의.m4a -o out.md   # 단발 실행
uv run python scripts/run_watcher.py            # watcher 데몬 단발 실행(launchd 없이)

# 테스트 / 린트 (CI 와 동일한 게이트 — .github/workflows/ci.yml)
uv run pytest                                   # 전체
uv run pytest tests/unit/test_summarize.py      # 파일 하나
uv run pytest tests/unit/test_summarize.py::test_이름 -v   # 테스트 하나
uv run black --check .                           # 포맷 검사 (line-length 120)
uv run isort --check-only .                      # import 정렬 검사
uv run flake8 .                                  # 린트 (max-line-length 120)
```

CI 는 black → isort → flake8 → pytest 순서로 돌고, py3.11/3.12 매트릭스다. `.ruff_cache`/`.mypy_cache` 가 보이지만 **CI·dev 의존성에 포함된 정식 도구는 black/isort/flake8/pytest 뿐**이다. 새 코드도 이 4개만 통과시키면 된다.

외부 바이너리(`ffmpeg`, `whisper-cli`, 모델 `.bin`)는 `./setup.sh` 가 멱등하게 빌드/다운로드한다. 단위 테스트는 subprocess 를 모킹하므로 CI·테스트에 vendor/ 의존성이 필요 없다.

## 아키텍처

### 파이프라인 단계 (단방향)

`src/pipeline.py:run_pipeline` 이 오케스트레이터다. 비싼 단계(전사/요약) 전에 키·의존성·입력파일을 **선검사해서 빠르게 실패**시키는 게 핵심 설계.

```
audio.py      ffmpeg 로 16kHz mono WAV 변환
transcribe.py whisper-cli subprocess 전사 → 파싱 → 신뢰도 게이트 + 완전성 검사
chunking.py   15분 + 30초 오버랩 시간경계 청킹 (순수 로직, 부수효과 없음)
summarize.py  OpenRouter Map-Reduce 요약 (429 백오프 + 모델 폴백)
report.py     Markdown 렌더 (순수 로직)
```

`chunking.py`·`report.py` 는 부수효과 없는 순수 함수라 테스트가 쉽다. I/O·subprocess·네트워크는 `audio`/`transcribe`/`summarize` 에 격리돼 있다.

### 설정이 곧 단일 진실원천

**매직넘버·모델명·임계값은 전부 `configs/pipeline.yaml` 에 모인다. 코드에 하드코딩 금지.** `src/config.py` 가 YAML 각 섹션을 `@dataclass(frozen=True)` 로 정규화하고 `.env`(`OPENROUTER_API_KEY`)를 로드한다. 새 튜닝 파라미터를 추가할 때는 ① yaml 에 주석과 함께 키 추가 → ② config.py 의 해당 dataclass 에 필드 추가 → ③ 코드에서 참조, 순서로 한다.

경로 해석 규칙(`config.py`): 상대경로는 **CWD 가 아니라 `PROJECT_ROOT` 기준**으로 절대화된다(launchd 가 임의 CWD 에서 실행하기 때문). 절대경로(컨테이너 `/data/...`)나 PATH 명령(`whisper-cli`)은 그대로 둔다.

### 핵심 설계 결정 (코드만 봐선 안 보이는 의도)

- **single-shot vs Map-Reduce 분기**: 전사 글자수가 `chunking.single_shot_max_chars`(기본 40000) 이하면 Map 을 건너뛰고 raw 전사를 한 번에 요약한다. Map-Reduce 는 부분요약→통합으로 정보를 두 번 깎아 긴 회의가 과도하게 짧아지는 문제(예: 51분→10줄)가 있어서, 폴백 모델 컨텍스트에 통째로 들어가는 길이는 이중압축을 피한다.
- **요약 회복탄력성**: 모델 폴백 체인(`summarize.models`)을 위에서부터 시도, 모델당 429/타임아웃 시 지수 백오프 재시도. Map 단계는 `max_chunk_failure_pct` 만큼 부분실패를 허용한다(0 이면 청크 하나라도 실패 시 전체 실패).
- **전사 캐시(issue #5)**: `src/cache.py` 가 원본 오디오 SHA-256 키로 전사 결과를 `data/transcripts` 에 보관 → 요약 등 후속 단계 실패 후 재처리 시 가장 비싼 전사를 건너뛴다. **캐시 실패는 절대 전사를 막지 않는다**(정확성 무관 최적화라 캐시 없이 폴백).
- **RTF ETA 자동보정**: `src/rtf_calibration.py` 가 실측 RTF 를 EMA 로 `.rtf_state` 에 누적해 다음 전사 ETA 를 하드웨어에 수렴시킨다. yaml 의 `rtf_estimate` 는 콜드스타트 초기값일 뿐.
- **신뢰도 게이트 / 완전성 검사**: `transcribe.py` 가 `no_speech_prob`/`avg_logprob` 로 무음·환각을 거르고, 오디오 길이 대비 마지막 세그먼트 end 가 `completeness_tolerance_sec` 를 넘게 잘리면 실패시킨다.

### 두 개의 진입점

- `scripts/run_pipeline.py` — 단발 CLI(`run_pipeline` 호출).
- `scripts/run_watcher.py` → `src/watcher.py` — `data/inbox` 폴링 데몬. 파일 크기 안정화로 업로드 완료를 감지, 처리 후 원본을 성공 시 `data/processed`/실패 시 `data/failed` 로 이동, 요약은 `data/output` 에. macOS 에선 `deploy/install-launchd.sh` 로 LaunchAgent 상시 실행(Metal 가속). `src/notify.py` 가 처리 결과를 macOS 알림센터로 띄운다(비-macOS 는 무해하게 무시).

### 예외 계층

`src/exceptions.py` 에 `PipelineError` 루트 아래 `DependencyError`/`CacheError` 등 단계별 예외가 있다. 새 실패 모드는 여기에 추가하고 CLI(`run_pipeline.py`)에서 `PipelineError` 로 잡아 사용자 메시지로 변환한다.

## 코딩 컨벤션

`coding_convention.md`(AgileSoda Algorithm Team) 준수: snake_case 함수/변수, PascalCase 클래스, UPPER_SNAKE_CASE 상수, 큰따옴표 문자열, 4 spaces, line-length 120. 함수 시그니처에 타입힌트 필수, 설정 객체는 `@dataclass(frozen=True)`(불변).

LLM 프롬프트는 코드가 아니라 `prompts/map_summary.txt`·`prompts/reduce_summary.txt` 텍스트 파일로 분리돼 있다. 프롬프트 튜닝은 이 파일에서.
