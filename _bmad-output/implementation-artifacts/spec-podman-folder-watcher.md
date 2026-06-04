---
title: 'Podman 폴더 Watcher — 녹음파일 드롭 → 요약 파이프라인 자동 실행'
type: 'feature'
created: '2026-06-04'
status: 'done'
baseline_commit: 'c7d049e'
context:
  - '{project-root}/coding_convention.md'
  - '{project-root}/src/pipeline.py'
  - '{project-root}/src/config.py'
  - '{project-root}/configs/pipeline.yaml'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** 지금은 회의 녹음을 요약하려면 매번 `run_pipeline.py`에 파일 경로를 직접 넘겨 수동 실행해야 한다. 녹음을 특정 폴더에 떨궈 두면 알아서 처리되는 무인 자동화가 없다.

**Approach:** Podman 컨테이너 안에서 watcher 데몬을 띄워 입력 폴더(inbox)를 주기적으로 polling 한다. 새 녹음파일이 업로드 완료(크기 안정화)되면 기존 `run_pipeline()`을 호출해 요약 리포트를 만들고, 원본을 성공 시 `processed/`, 실패 시 `failed/`로 이동한다. STT(whisper.cpp)·요약까지 전부 한 컨테이너에서 자립적으로 동작한다.

## Boundaries & Constraints

**Always:**
- 기존 `run_pipeline()`을 그대로 재사용(요약 로직 재구현 금지).
- 모든 매직넘버(폴링 주기·안정화 임계·확장자·디렉토리)는 `configs/pipeline.yaml`의 `watcher` 섹션에 모은다. 하드코딩 금지.
- 업로드 중 파일은 처리 안 함: 크기+mtime이 연속 N회 동일할 때만 "안정"으로 간주.
- 한 번에 한 파일씩 순차 처리(자원 경쟁 방지).
- 컨테이너 whisper-cli/모델 경로를 호스트 config(`vendor/whisper.cpp/...`)와 동일하게 맞춰 `stt` 섹션 수정 불필요.
- SIGINT/SIGTERM 시 진행 중 파일만 끝내고 graceful 종료.

**Ask First:**
- whisper.cpp를 컨테이너 CPU 빌드 외 방식(사전빌드 번들 등)으로 바꿀 경우.
- 모델(1.6GB)을 볼륨 마운트 대신 이미지에 굽는 방식으로 바꿀 경우.

**Never:**
- inotify/watchdog 같은 이벤트 기반 감지(볼륨 마운트 FS 신뢰성 문제로 polling만 사용).
- 요약 파이프라인 내부 로직(STT·청킹·OpenRouter) 변경.
- 컨테이너→호스트 명령 실행 연동(전부 컨테이너 내부에서 처리).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| 정상 처리 | inbox에 안정화된 `.m4a` 1개 | `output/<stem>.md` 생성, 원본 → `processed/` 이동 | N/A |
| 업로드 중 | 크기가 계속 변하는 파일 | 안정될 때까지 skip, 다음 주기 재확인 | N/A |
| 지원 안 함 | `.txt` 등 비대상 확장자 | 무시(이동·처리 안 함) | 로그만 debug |
| 파이프라인 실패 | 빈 파일/무음/STT 실패 | 원본 → `failed/` 이동, 에러 로그 | `PipelineError`/`FileNotFoundError` catch, 다음 파일 계속 |
| 이름 충돌 | `processed/`에 동일명 존재 | 타임스탬프 suffix 붙여 이동(`name_20260604-101530.m4a`) | N/A |
| 종료 신호 | 처리 중 SIGTERM | 현재 파일 끝내고 루프 종료, exit 0 | N/A |

</frozen-after-approval>

## Code Map

- `src/pipeline.py` -- `run_pipeline(input, output, config)` 재사용 대상(수정 없음).
- `src/config.py` -- `WatcherConfig` dataclass + `_build_config`에 watcher 파싱 추가.
- `src/exceptions.py` -- 기존 `PipelineError` 계층을 watcher가 catch.
- `configs/pipeline.yaml` -- `watcher` 섹션 신규 추가.
- `src/watcher.py` -- 신규. polling·안정화·중복방지·파일이동 핵심 로직.
- `scripts/run_watcher.py` -- 신규. watcher 데몬 CLI 진입점 + 시그널 핸들링.
- `Containerfile` -- 신규. linux용 whisper.cpp 빌드 + ffmpeg + python deps.
- `compose.yaml` -- 신규. 볼륨 마운트(inbox/processed/failed/output/모델/.env) 정의.

## Tasks & Acceptance

**Execution:**
- [x] `configs/pipeline.yaml` -- `watcher` 섹션 추가(inbox/processed/failed/output_dir, poll_interval_sec=10, stability_checks=2, extensions 목록) -- 설정 중앙화.
- [x] `src/config.py` -- `WatcherConfig` frozen dataclass + `PipelineConfig.watcher` 필드 + `_build_config` 파싱 + `_resolve_dir` 헬퍼 추가 -- 타입 안전 설정.
- [x] `src/watcher.py` -- 폴더 스캔→안정화 판정→`run_pipeline` 호출→processed/failed 이동 루프. snapshots/stable_counts 추적으로 재진입 방지·정리. 이름충돌 시 타임스탬프 suffix -- watcher 핵심.
- [x] `scripts/run_watcher.py` -- argparse(`-c config`, `-v`) + 로깅 + SIGINT/SIGTERM 핸들러로 graceful stop -- 데몬 진입점.
- [x] `Containerfile` -- 베이스 python:3.12-slim, apt로 ffmpeg+빌드툴 설치, whisper.cpp clone+CPU 빌드(동일 경로), pip로 deps 설치, CMD는 run_watcher -- 자립 이미지.
- [x] `compose.yaml` -- 볼륨(`./data/inbox`,`processed`,`failed`,`output`, 모델 `.bin`) 마운트 + `.env` env_file + restart 정책 -- 운영 편의.
- [x] `tests/unit/test_watcher.py` -- I/O 매트릭스 엣지케이스 단위 테스트(`run_pipeline` mock, `tmp_path` fixture) -- 회귀 방지.
- [x] `.gitignore` / `README.md` -- `data/` ignore 추가 + 컨테이너 실행법 문서화.

**Acceptance Criteria:**
- Given watcher 컨테이너 실행 중, when inbox에 녹음파일을 떨구면, then 업로드 완료 후 자동으로 `output/`에 요약 `.md`가 생기고 원본이 `processed/`로 이동한다.
- Given 파이프라인이 실패하는 파일, when 처리되면, then watcher는 죽지 않고 원본을 `failed/`로 옮긴 뒤 다음 파일을 계속 처리한다.
- Given `podman build` + `compose up`, when 호스트 모델 `.bin`을 마운트하면, then config 수정 없이 컨테이너 내부 whisper-cli 경로로 STT가 동작한다.

## Spec Change Log

## Design Notes

**안정화 판정:** 각 파일 `(size, mtime)`을 직전 스냅샷과 비교, `stability_checks`회 연속 동일하면 처리. 폴링 한 바퀴(`poll_interval_sec`)가 곧 체크 간격.

**경로 전략:** watcher 디렉토리 4종은 컨테이너 절대경로(`/data/inbox` 등) 디폴트 + compose 마운트. 절대경로는 그대로, 상대경로만 `PROJECT_ROOT` 기준 해석(`_resolve_resource`는 단일토큰 전용이라 미사용).

**whisper 플랫폼:** 호스트 `setup.sh`는 macOS Metal(Mach-O)이라 linux 컨테이너 실행 불가 → 컨테이너는 `-DGGML_METAL=OFF` CPU 빌드를 동일 경로에 생성. 모델 `.bin`만 마운트(플랫폼 독립).

## Verification

**Commands:**
- `uv run pytest tests/unit/test_watcher.py -v` -- expected: 전 케이스 통과
- `uv run black --check src/ scripts/ && uv run flake8 src/ scripts/` -- expected: 위반 0
- `podman build -t meeting-watcher -f Containerfile .` -- expected: 이미지 빌드 성공

**Manual checks:**
- `compose up` 후 inbox에 짧은 테스트 녹음 복사 → 수 초 내 `output/*.md` 생성 + 원본 `processed/` 이동 확인.
- 빈 `.m4a` 복사 → `failed/`로 이동되고 컨테이너 로그에 에러, watcher 계속 살아있음 확인.

## Suggested Review Order

**진입점 — 데몬 기동과 종료**

- 설정 로드 → watcher 생성 → SIGINT/SIGTERM 핸들러 등록 → 블로킹 실행. 전체 흐름의 출발점.
  [`run_watcher.py:45`](../../scripts/run_watcher.py#L45)
- 시그널을 받으면 stop 플래그만 세워 graceful 종료(진행 중 파일은 마침).
  [`run_watcher.py:63`](../../scripts/run_watcher.py#L63)

**watcher 핵심 로직**

- 폴링 루프: 스캔 → stop 신호에 반응하는 분할 sleep 반복.
  [`watcher.py:53`](../../src/watcher.py#L53)
- 업로드 완료 판정: `(size, mtime)` 가 연속 N회 동일해야 안정.
  [`watcher.py:114`](../../src/watcher.py#L114)
- 처리 분기: 성공→processed, 도메인 실패/예상외 예외→failed. 데몬 생존이 우선(poison-pill 방지).
  [`watcher.py:133`](../../src/watcher.py#L133)
- 이름충돌 시 타임스탬프 suffix, cross-fs 폴백 이동.
  [`watcher.py:157`](../../src/watcher.py#L157)

**설정 — 중앙화와 경계값 검증**

- watcher 값 검증(poll≥1, stability≥1, extensions 비어있지 않음) 후 빌드 — 오설정 즉시 실패.
  [`config.py:210`](../../src/config.py#L210)
- 절대경로는 그대로, 상대경로만 루트 기준 해석(컨테이너 `/data/*` 디폴트).
  [`config.py:36`](../../src/config.py#L36)
- watcher 섹션(폴링·안정화·확장자·디렉토리) 매직넘버 중앙화.
  [`pipeline.yaml:61`](../../configs/pipeline.yaml#L61)

**컨테이너 — 자립 이미지와 마운트**

- linux CPU 빌드의 whisper-cli 를 호스트 config 와 동일 경로에 생성(Metal 빌드 비호환 회피).
  [`Containerfile:17`](../../Containerfile#L17)
- 모델 `.bin` 만 볼륨 마운트(플랫폼 독립), 입출력 폴더는 `/data/*` 에 바인드.
  [`compose.yaml:17`](../../compose.yaml#L17)

**테스트 (지원 검증)**

- 안정화→처리→이동 happy path, 예상외 예외 생존, config 경계값 거부.
  [`test_watcher.py:49`](../../tests/unit/test_watcher.py#L49)
