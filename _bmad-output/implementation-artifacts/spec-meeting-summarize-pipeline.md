---
title: '회의 요약 파이프라인 (whisper.cpp + OpenRouter)'
type: 'feature'
created: '2026-06-04'
status: 'done'
context:
  - '{project-root}/_bmad-output/planning-artifacts/research/technical-meeting-summarize-tech-stack-research-2026-06-04.md'
  - '{project-root}/coding_convention.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** 개인 회의 녹음을 수동으로 듣고 정리하는 게 번거롭다. 로컬 STT + 무료 LLM으로 녹음 파일 하나를 넣으면 구조화된 요약(핵심/결정/액션아이템)이 나오는 자동 파이프라인이 없다.

**Approach:** Python 단일 CLI 파이프라인. ffmpeg로 16kHz WAV 변환 → whisper.cpp(subprocess, JSON 출력)로 전사 → 세그먼트를 시간/발화 경계로 청킹 → OpenRouter 무료티어로 Map-Reduce 요약 → Markdown 리포트 출력. 외부 바이너리 설치는 setup.sh로 자동화.

## Boundaries & Constraints

**Always:**
- coding_convention.md 준수: src/ 레이아웃, 타입힌트, Google docstring, `logging`(print 금지), custom 예외(`src/exceptions.py`), 매직넘버는 config로.
- OpenRouter 키는 `.env`의 `OPENROUTER_API_KEY`로만 로드. 하드코딩/커밋 금지(`.gitignore`).
- LLM 프롬프트는 `prompts/*.txt`로 분리. 모델명·청크크기·폴백체인은 `configs/pipeline.yaml`로.
- 외부 호출(ffmpeg/whisper subprocess, OpenRouter API)은 전부 에러 처리 + 친절한 메시지.
- OpenRouter 호출은 429 지수 백오프 재시도 후 모델 폴백 체인, 최종 실패 시 명시적 예외.
- subprocess(ffmpeg/whisper) 호출은 타임아웃을 설정하고, exit code뿐 아니라 출력 내용·완전성까지 검증(무음/환각·잘린 JSON 조용한 실패 방지).

**Ask First:**
- whisper 모델 변경(large-v3-turbo 외) 또는 유료/비-`:free` 모델 추가.
- 회의 원문(전사 텍스트)을 OpenRouter 외 외부로 전송하는 어떤 추가 연동.

**Never:**
- 서버/REST/웹UI/DB. 개인용 CLI 한정 (KISS).
- 화자 분리(diarization), 실시간 스트리밍, 다국어 동시 처리 — 범위 밖.
- 음성을 Whisper API 등 클라우드 STT로 보내는 것 (로컬 STT 목표).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| 정상 | 유효한 `.m4a/.wav` + 키 설정됨 | `핵심요약/결정사항/액션아이템` 3섹션 Markdown 리포트 생성 | N/A |
| 긴 회의 | 15분 초과 전사 | 15분+오버랩 청크로 Map → 통합 Reduce 요약 | N/A |
| 입력 파일 없음 | 존재하지 않는 경로 | 즉시 중단 | `FileNotFoundError` 명확 메시지 |
| 바이너리 미설치 | ffmpeg/whisper-cli PATH 없음 | 즉시 중단 + `setup.sh` 안내 | custom 예외, 설치 명령 안내 |
| 키 없음 | `OPENROUTER_API_KEY` 미설정 | 시작 시 즉시 중단 | custom 예외, 설정 방법 안내 |
| 429 rate limit | OpenRouter 429 | 지수 백오프 재시도 → 모델 폴백 → 최종 실패 시 예외 | 재시도 로그 + 폴백 |
| 빈 전사 | 세그먼트 0개 | 빈 요약 대신 "전사 결과 없음" 명시 후 중단 | 경고 로그 |
| 무음/환각 | 정상 exit지만 무의미 전사(무음→환각 텍스트) | `no_speech_prob`·평균 logprob 게이트로 "유효 음성 없음" 판정 후 중단 | 경고 로그 |
| 전사 중단 | whisper 타임아웃/크래시로 잘린 JSON | 오디오 총 길이 vs 마지막 세그먼트 `end` 비교, 불일치 시 실패(조용한 누락 방지) | `TranscriptionError` |
| Map 부분 실패 | 일부 청크만 요약 성공 | 정책에 따라 누락 청크 명시 후 Reduce 진행 또는 전체 실패 | 명시적 처리 + 로그 |

</frozen-after-approval>

## Code Map

- `pyproject.toml` -- uv 프로젝트 설정, 의존성(openai, python-dotenv, pyyaml), black/isort/flake8/pytest 설정(line 120)
- `setup.sh` -- brew로 ffmpeg 설치 + whisper.cpp 클론/빌드 + large-v3-turbo 모델 다운로드
- `.env.example` / `.gitignore` -- 키 템플릿 / 시크릿·산출물 제외
- `configs/pipeline.yaml` -- STT 모델경로·언어, 요약 모델 폴백체인·재시도·타임아웃, 청크 분/오버랩, 샘플레이트, STT 신뢰도 게이트 임계값(`no_speech_prob`·평균 logprob), Map 부분실패 허용 임계(누락률 %), subprocess 타임아웃
- `prompts/map_summary.txt` / `prompts/reduce_summary.txt` -- 청크별 부분요약 / 통합요약 프롬프트(한국어 출력 명시)
- `src/exceptions.py` -- `PipelineError` 기반 custom 예외(`DependencyError`, `TranscriptionError`, `SummarizationError`)
- `src/config.py` -- YAML 로드 + dataclass 설정, `.env` 로드
- `src/audio.py` -- ffmpeg subprocess로 16kHz mono WAV 변환
- `src/transcribe.py` -- whisper-cli subprocess 실행 + JSON 파싱 → `list[Segment]`
- `src/chunking.py` -- 세그먼트를 시간경계+오버랩으로 청크 분할(순수 로직)
- `src/summarize.py` -- OpenRouter Map-Reduce, 429 백오프 + 모델 폴백
- `src/report.py` -- 요약 dict → Markdown 리포트 렌더(순수 로직)
- `src/pipeline.py` -- 전체 오케스트레이션 + 의존성/키 선검사
- `scripts/run_pipeline.py` -- argparse CLI 진입점, `logging.basicConfig`

## Tasks & Acceptance

**Execution:**
- [x] `pyproject.toml`, `.gitignore`, `.env.example`, `README.md` -- uv 프로젝트 부트스트랩 + 실행/설치법
- [x] `setup.sh` -- ffmpeg + whisper.cpp 빌드 + 모델 다운로드 자동화 (멱등하게)
- [x] `configs/pipeline.yaml`, `prompts/map_summary.txt`, `prompts/reduce_summary.txt` -- 설정·프롬프트 분리
- [x] `src/exceptions.py`, `src/config.py` -- 예외 계층 + 설정/`.env` 로더
- [x] `src/audio.py` -- WAV 변환 (ffmpeg 미설치 시 `DependencyError`)
- [x] `src/transcribe.py` -- whisper-cli 호출 + JSON→`Segment` 파싱
- [x] `src/chunking.py` -- 15분+오버랩 청킹, 발화 경계 보존
- [x] `src/summarize.py` -- Map-Reduce + 429 백오프 + 폴백체인
- [x] `src/report.py` -- 3섹션 Markdown 렌더
- [x] `src/pipeline.py`, `scripts/run_pipeline.py` -- 오케스트레이션 + CLI
- [x] `tests/unit/test_chunking.py`, `tests/unit/test_report.py`, `tests/unit/test_transcribe.py`, `tests/unit/test_summarize.py` -- I/O 매트릭스 엣지케이스(청킹 경계·오버랩, 빈 세그먼트, 리포트 렌더, JSON 파싱, 무음/환각 신뢰도 게이트, 잘린 JSON 완전성 검사, Map 부분실패·폴백 전환) 단위 테스트

**Acceptance Criteria:**
- Given 키 미설정, when 파이프라인 시작, then 전사 전에 즉시 명확한 예외로 중단된다.
- Given ffmpeg/whisper-cli 미설치, when 실행, then `setup.sh` 안내와 함께 중단된다.
- Given 유효 녹음 파일, when 파이프라인 완료, then 출력 경로에 핵심/결정/액션 3섹션 Markdown이 존재한다.
- Given OpenRouter가 첫 모델에 429, when 백오프 소진, then 다음 폴백 모델로 전환 후 성공하거나 명확히 실패한다.
- Given 무음/환각 전사, when 신뢰도 게이트 통과, then "유효 음성 없음"으로 중단된다.
- Given 잘린 전사 JSON, when 완전성 검사, then 오디오 길이 불일치로 `TranscriptionError`가 발생한다.
- Given Map 일부 청크 실패, when 누락률이 허용 임계 이하, then 누락을 명시한 채 Reduce를 진행한다.
- `pytest tests/ -v`가 통과하고, 청킹/리포트/파싱 단위 테스트가 엣지케이스를 커버한다.

## Design Notes

- STT 호출 방식(ADR): whisper-cli **subprocess** 선택 — 의존성 격리·setup.sh 빌드 통제·KISS가 이유. `faster-whisper`(in-process, 보통 더 빠름·핸들링 쉬움) 대비 트레이드오프는 프로세스 경계 오버헤드·JSON 파싱·타임아웃 직접 관리를 감수하는 대신 무거운 ML 의존성을 피하는 것. 상세 근거는 context의 tech-stack research 문서 참조.
- STT→요약 계약: whisper JSON의 `segments[].{start,end,text}`가 단일 진실 소스. `Segment` dataclass로 정규화 후 청킹·리포트가 소비.
- whisper-cli 호출 예: `whisper-cli -m <model> -f audio.wav -l ko -oj -of <prefix>` → `<prefix>.json` 파싱.
- OpenRouter는 OpenAI SDK 드롭인: `OpenAI(base_url="https://openrouter.ai/api/v1", api_key=...)`. 폴백은 `models` 리스트 순회, 각 모델에서 429/타임아웃은 지수 백오프(`2**attempt`).
- Map-Reduce: 각 청크 → `map_summary.txt` 부분요약; 부분요약들을 합쳐 `reduce_summary.txt`로 통합. 프롬프트에 "한국어로 요약" 명시.
- 단일 청크 최적화(ADR): 전사가 단일 청크(컨텍스트 임계 이하)에 들어오면 Map을 생략하고 single-shot 요약으로 분기 — 맥락 보존·쿼터 절약 둘 다 이득.
- 부분요약 캐시 범위(ADR): 청크별 부분요약 캐시는 **단일 실행 내 메모리/임시디렉터리 한정**이며 실행 간 디스크 영속화는 하지 않음 — Never의 DB/영속 저장 금지와 정합.
- Map 부분 실패 처리: 일부 청크 요약 실패 시 정책 명시 — 누락 청크를 리포트에 표시하고 Reduce 진행하거나, 누락률이 임계 초과 시 전체 실패. 허용 임계(누락률 %)는 `pipeline.yaml` 설정값이며 기본 0%(하나라도 실패 시 전체 실패). 청크별 부분요약을 캐싱해 재시도 시 성공분 재사용.
- 폴백 체인 독립성 주의: 폴백 모델이 전부 `:free`면 공용 쿼터라 상관 장애(동시 429) 가능성이 높아 단일 장애점이 됨. 최소 1개를 다른 제공자/유료 fallback으로 두는 것은 "Ask First" 대상.

## Verification

**Commands:**
- `uv sync` -- expected: 의존성 설치 성공
- `uv run pytest tests/ -v` -- expected: 전체 통과
- `uv run black --check --line-length 120 src/ && uv run flake8 src/ --max-line-length 120` -- expected: 포맷·린트 통과
- `uv run python scripts/run_pipeline.py <샘플녹음> --output out.md` -- expected: `out.md`에 3섹션 리포트 (수동, 바이너리·키 설정 후)

## Suggested Review Order

**오케스트레이션 (진입점)**

- 전체 흐름 한눈에 — 의존성/키 선검사 후 변환→전사→청킹→요약→리포트
  [`pipeline.py:29`](../../src/pipeline.py#L29)

**STT 신뢰성 (리뷰 핵심 — `-ojf` 수정)**

- subprocess 실행 + 완전성·신뢰도 검증을 묶는 전사 진입점
  [`transcribe.py:124`](../../src/transcribe.py#L124)
- `-oj -ojf` 로 토큰 포함 full JSON 출력 — 이게 빠져서 게이트가 무력화됐었음
  [`transcribe.py:162`](../../src/transcribe.py#L162)
- 무음/환각 유효비율 게이트 (whisper.cpp 한계상 logprob 의존)
  [`transcribe.py:103`](../../src/transcribe.py#L103)

**요약 레질리언스 (HIGH 수정)**

- 인증=즉시중단 / bad-request=다음모델 / 일시오류=백오프 재시도로 분류
  [`summarize.py:129`](../../src/summarize.py#L129)
- 빈 `choices` 응답 가드 (콘텐츠필터/쿼터 대비)
  [`summarize.py:176`](../../src/summarize.py#L176)
- single-shot 분기 + Map-Reduce 본체
  [`summarize.py:35`](../../src/summarize.py#L35)

**청킹**

- 입력 정렬 보장 + 오버랩 경계 포함(0초 세그먼트 보존)
  [`chunking.py:36`](../../src/chunking.py#L36)

**설정 / 경로**

- YAML + `.env` 로드, API 키 선검사
  [`config.py:99`](../../src/config.py#L99)
- 상대경로를 프로젝트 루트 기준으로 해석(어느 CWD에서도 동작)
  [`config.py:25`](../../src/config.py#L25)

**주변부**

- 모델·임계·경로 단일 소스 (코드 하드코딩 금지)
  [`pipeline.yaml:10`](../../configs/pipeline.yaml#L10)
- ffmpeg + whisper.cpp 빌드 + 모델 멱등 설치
  [`setup.sh:1`](../../setup.sh#L1)
- 에러 분류·청킹 엣지·게이트 한계 커버 테스트
  [`test_summarize.py:54`](../../tests/unit/test_summarize.py#L54)
