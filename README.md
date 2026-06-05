# 회의 요약 파이프라인

[![Platform](https://img.shields.io/badge/platform-macOS%20(Apple%20Silicon)-000000?logo=apple&logoColor=white)](https://www.apple.com/mac/)
[![Python](https://img.shields.io/badge/python-3.11+-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Release](https://img.shields.io/github/v/release/kkalla/meeting-summarize)](https://github.com/kkalla/meeting-summarize/releases/latest)
[![License: MIT](https://img.shields.io/github/license/kkalla/meeting-summarize)](LICENSE)
[![Linting: Ruff](https://img.shields.io/badge/linting-ruff-261230?logo=ruff&logoColor=white)](https://github.com/astral-sh/ruff)
[![Code style: Black](https://img.shields.io/badge/code%20style-black-000000)](https://github.com/psf/black)

개인 회의 녹음 파일 하나를 넣으면 **로컬 STT(whisper.cpp) + OpenRouter 무료티어 LLM**으로
구조화된 요약(핵심 / 결정사항 / 액션아이템)을 Markdown 으로 뽑아주는 단일 CLI 파이프라인.

```
녹음(.m4a/.wav)
  → ffmpeg (16kHz mono WAV)
  → whisper.cpp (subprocess, JSON 전사)
  → 청킹 (15분 + 오버랩, 발화 경계 보존)
  → OpenRouter Map-Reduce 요약 (429 백오프 + 모델 폴백)
  → Markdown 리포트
```

음성은 외부로 나가지 않고 로컬에서 전사된다. 전사 텍스트만 요약을 위해 OpenRouter 로 전송된다.

## 1. 외부 의존성 설치

ffmpeg, whisper.cpp 빌드, large-v3-turbo 모델 다운로드를 자동화 (멱등):

```bash
./setup.sh
```

빌드된 `whisper-cli` 가 PATH 에 없으면 `configs/pipeline.yaml` 의 `stt.whisper_cli` 를
출력된 절대경로로 맞추거나, 안내된 `ln -sf` 명령으로 PATH 에 등록한다.

## 2. Python 환경 + API 키

```bash
uv sync
cp .env.example .env       # .env 에 OpenRouter 키 입력 (https://openrouter.ai/keys)
```

`.env` 는 절대 커밋하지 않는다(.gitignore 에 포함).

## 3. 실행

```bash
uv run python scripts/run_pipeline.py 회의녹음.m4a --output out.md
```

옵션:

- `-o, --output` : 출력 Markdown 경로 (기본 `out.md`)
- `-c, --config` : 설정 YAML 경로 (기본 `configs/pipeline.yaml`)
- `-v, --verbose` : 디버그 로그

## 4. 자동화: 폴더 watcher (podman)

수동 실행 대신, **컨테이너를 띄워두고 폴더에 녹음만 떨구면** 자동으로 요약되게 할 수 있다.
`data/inbox` 에 파일이 올라오면 watcher 가 업로드 완료(크기 안정화)를 감지해 파이프라인을
돌리고, 원본을 성공 시 `data/processed`, 실패 시 `data/failed` 로 옮긴다. 요약은 `data/output` 에 생긴다.

사전 준비: ① `./setup.sh` 로 모델(.bin)을 받아두고, ② `.env` 에 `OPENROUTER_API_KEY` 를 채운다.
(whisper.cpp 는 호스트가 macOS Metal 빌드라 컨테이너에선 못 쓰므로, 이미지 빌드 시 컨테이너 안에서
CPU 로 다시 빌드한다. 모델 `.bin` 만 호스트 빌드본을 볼륨 마운트한다.)

```bash
podman compose up -d --build      # 최초 빌드 후 백그라운드 실행
podman compose logs -f watcher    # 처리 로그 확인
cp 회의녹음.m4a data/inbox/        # 떨구면 수 초 내 자동 처리
podman compose down               # 중지(진행 중 작업은 마치고 graceful 종료)
```

watcher 설정(폴링 주기·안정화 횟수·대상 확장자·폴더 경로)은 `configs/pipeline.yaml` 의 `watcher` 섹션에서 조정한다.
직접(컨테이너 없이) 돌리려면 `uv run python scripts/run_watcher.py` 도 가능하다.

## 설정 (`configs/pipeline.yaml`)

STT 모델/언어, 요약 모델 폴백 체인·재시도·타임아웃, 청크 길이·오버랩,
무음/환각 신뢰도 게이트 임계값, Map 부분실패 허용 누락률 등을 조정한다.
모델명·임계값 등 매직넘버는 전부 이 파일에 모여 있다.

## 개발

```bash
uv run pytest tests/ -v
uv run black --check --line-length 120 src/ tests/
uv run flake8 src/ tests/ --max-line-length 120
```

## 구조

```
configs/pipeline.yaml      설정(모델·임계값·청크)
prompts/                   Map / Reduce LLM 프롬프트
src/
  config.py                YAML + .env 로더 (dataclass)
  exceptions.py            custom 예외 계층
  audio.py                 ffmpeg WAV 변환
  transcribe.py            whisper-cli 전사 + 파싱 + 신뢰도/완전성 검사
  chunking.py              시간 경계 + 오버랩 청킹 (순수 로직)
  summarize.py             OpenRouter Map-Reduce + 백오프/폴백
  report.py                Markdown 리포트 렌더 (순수 로직)
  pipeline.py              오케스트레이션 + 선검사
  watcher.py               폴더 폴링 watcher (안정화·중복방지·이동)
scripts/run_pipeline.py    CLI 진입점 (단발 실행)
scripts/run_watcher.py     watcher 데몬 진입점 (시그널 핸들링)
Containerfile              watcher 이미지 (whisper.cpp CPU 빌드)
compose.yaml               podman/docker compose (볼륨 마운트)
tests/unit/                단위 테스트
```

## 범위 밖 (의도적으로 안 함)

서버/REST/웹UI/DB, 화자 분리(diarization), 실시간 스트리밍, 클라우드 STT 전송.
개인용 CLI 한정 (KISS).
