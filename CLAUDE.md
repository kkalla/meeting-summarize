# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

회의 녹음 파일을 받아 **OpenRouter STT + OpenRouter 무료티어 LLM**으로 구조화된 Markdown 요약을 뽑는 개인용 도구. **현재 주력 사용법은 Slack DM 봇**(`src/slack_bot/`, Socket Mode)이다 — Slack 에 오디오 파일을 올리면 자동으로 전사+요약해 `.md` 파일로 돌려준다.

로컬 whisper.cpp 기반 CLI 파이프라인(과거 주력이었던 방식)은 `legacy/` 에 보존돼 있다. 더 이상 활발히 개발되진 않지만 그대로 동작하며, 요약 단계(`src/summarize.py`/`src/report.py`)는 Slack 봇과 공유한다. 자세한 내용은 `legacy/README.md` 참고.

서버/REST/웹UI/DB/화자분리/실시간 스트리밍은 의도적으로 범위 밖(KISS) — 단, Slack 봇의 Socket Mode 연결이나 `files_upload_v2` 같은 Slack API 호출 자체는 이 KISS 제약의 예외다(봇이 성립하려면 필요한 최소 기능이므로).

## 명령어

`uv` 기반. 패키지는 `src` 가 곧 설치 대상(`[tool.hatch.build.targets.wheel] packages = ["src"]`)이라 `from src.xxx import ...` 로 import 하고, pytest 는 `pythonpath = ["."]` 로 루트에서 import 를 해석한다. `legacy/` 도 같은 방식으로 `from legacy.xxx import ...` 로 import한다.

```bash
uv sync --dev                                   # 의존성 설치

# 주력: Slack 봇 (Socket Mode)
uv run python scripts/run_slack_bot.py

# legacy: 로컬 whisper.cpp CLI (아래 "legacy 파이프라인" 참고)
uv run python legacy/run_pipeline.py 회의.m4a -o out.md
uv run python legacy/run_watcher.py

# 테스트 / 린트 (CI 와 동일한 게이트 — .github/workflows/ci.yml)
uv run pytest                                   # 전체(tests/unit + tests/legacy)
uv run pytest tests/unit/test_summarize.py      # 파일 하나
uv run pytest tests/unit/test_summarize.py::test_이름 -v   # 테스트 하나
uv run black --check .                           # 포맷 검사 (line-length 120)
uv run isort --check-only .                      # import 정렬 검사
uv run flake8 .                                  # 린트 (max-line-length 120)
```

CI 는 black → isort → flake8 → pytest 순서로 돌고, py3.11/3.12 매트릭스다. `.ruff_cache`/`.mypy_cache` 가 보이지만 **CI·dev 의존성에 포함된 정식 도구는 black/isort/flake8/pytest 뿐**이다. 새 코드도 이 4개만 통과시키면 된다.

legacy 파이프라인을 쓰려면 외부 바이너리(`ffmpeg`, `whisper-cli`, 모델 `.bin`)가 필요하고 `./setup.sh` 가 멱등하게 빌드/다운로드한다. Slack 봇은 이 바이너리가 필요 없다(OpenRouter STT API 호출만). 단위 테스트는 subprocess/네트워크를 모킹하므로 CI·테스트엔 둘 다 필요 없다.

## 아키텍처

### Slack 봇 (주력, `src/slack_bot/`)

`scripts/run_slack_bot.py` → `src/slack_bot/bot.py`. Slack DM 으로 오디오 파일을 올리면 자동으로 전사+요약해 `.md` 파일을 결과로 올려주는 개인용 봇. 설정은 `configs/slack_bot.yaml`/`src/slack_bot/config.py` 에 분리돼 있고, 요약 단계는 `configs/pipeline.yaml` 의 `summarize` 섹션을 legacy 파이프라인과 공유한다.

```
src/slack_bot/config.py           configs/slack_bot.yaml + .env(SLACK_BOT_TOKEN/SLACK_APP_TOKEN) 로딩
src/slack_bot/openrouter_stt.py   OpenRouter STT(microsoft/mai-transcribe-1.5) 호출, 429/5xx 재시도
src/slack_bot/processor.py        Slack 파일 다운로드 → STT → src.summarize/src.report 재사용 → .md 저장
src/slack_bot/bot.py              slack_bolt App + SocketModeHandler, 이벤트 라우팅
```

- **Socket Mode**: 공개 URL/ngrok 불필요. 봇이 Slack 으로 WebSocket 을 먼저 열어(App-Level Token, `xapp-`) 이벤트를 받는다. `SLACK_BOT_TOKEN`(`xoxb-`)/`SLACK_APP_TOKEN`(`xapp-`)이 `.env` 에 필요(`OPENROUTER_API_KEY` 는 legacy 파이프라인과 공유).
- **전사는 OpenRouter STT, 요약은 재사용**: STT 는 로컬 whisper.cpp 대신 OpenRouter STT(`microsoft/mai-transcribe-1.5`, 유료·시간당 약 $0.36 — 이 프로젝트의 "무료티어" 원칙은 요약(LLM) 단계에만 적용된다)를 쓴다. 요약은 `src/summarize.py`/`src/report.py` 를 그대로 재사용한다 — 요약 로직 이중 유지 방지. OpenRouter STT 는 세그먼트 타임스탬프를 안 주므로 전체 텍스트를 청크 1개로 감싸 넘긴다(`Chunk(segments=())`).
- **⚠️ 긴 오디오 타임아웃 미해결**: OpenRouter STT 문서상 업스트림 프로바이더 타임아웃이 60초라, 현재처럼 파일 전체를 한 번에 base64 인코딩해 보내면 긴 회의(1시간+)에서 타임아웃날 위험이 있다. 아직 오디오 분할 로직이 없다(TODO — 자세한 내용은 리포지토리 루트의 `TODO.md` 참고).
- **비동기 처리 필수**: Slack Events API 는 3초 내 처리를 기대하고, 넘기면 이벤트가 재전송돼 같은 파일이 중복 처리될 위험이 있다. `bot.py` 의 이벤트 핸들러는 ack 메시지만 즉시 보내고 실제 처리(다운로드/전사/요약)는 `ThreadPoolExecutor` 로 넘긴다. `event_ts` 기준 최근 이벤트 캐시로 재전송 중복도 한 번 더 막는다.
- **봇 자기메시지 필터링**: `bot_id`/`subtype=bot_message` 이벤트는 무시한다(안 하면 봇이 올린 결과 파일에 봇 자신이 반응하는 무한루프 위험).
- **트리거 범위**: 봇과의 DM(`channel_type == "im"`)에 오디오 파일이 첨부된 경우만 처리한다. 채널 업로드는 스코프 밖.
- **결과 전달**: `files_upload_v2` 로 `.md` 리포트를 스레드에 파일로 올린다(메시지 길이 제한 없이 긴 회의도 그대로 전달).

### core (Slack 봇 + legacy 파이프라인 공유, `src/`)

```
src/config.py      configs/pipeline.yaml 파싱(@dataclass(frozen=True)) + .env(OPENROUTER_API_KEY)
src/chunking.py     Chunk 데이터클래스(요약 최소 단위: index + text). 시간창 분할 로직은 legacy 전용(legacy/chunking.py)
src/summarize.py    OpenRouter Map-Reduce 요약 (429 백오프 + 모델 폴백)
src/report.py       Markdown 렌더 (순수 로직)
src/exceptions.py   PipelineError 루트의 예외 계층
src/notify.py       macOS 알림센터(legacy watcher 전용, 비-macOS 는 무해하게 무시)
```

`configs/pipeline.yaml` 의 `summarize` 섹션과 `chunking.single_shot_max_chars` 는 Slack 봇도 읽는다. `audio`/`stt`/`watcher`/`cache` 섹션은 legacy 파이프라인 전용이다.

### 설정이 곧 단일 진실원천

**매직넘버·모델명·임계값은 전부 YAML 에 모인다. 코드에 하드코딩 금지.** `src/config.py` 가 `configs/pipeline.yaml` 을, `src/slack_bot/config.py` 가 `configs/slack_bot.yaml` 을 각각 `@dataclass(frozen=True)` 로 정규화한다. 새 튜닝 파라미터를 추가할 때는 ① yaml 에 주석과 함께 키 추가 → ② 해당 config.py 의 dataclass 에 필드 추가 → ③ 코드에서 참조, 순서로 한다.

경로 해석 규칙: 상대경로는 **CWD 가 아니라 `PROJECT_ROOT` 기준**으로 절대화된다(launchd/데몬이 임의 CWD 에서 실행하기 때문). 절대경로(컨테이너 `/data/...`)나 PATH 명령(`whisper-cli`)은 그대로 둔다.

### 핵심 설계 결정 (코드만 봐선 안 보이는 의도)

- **single-shot vs Map-Reduce 분기**: 전사 글자수가 `chunking.single_shot_max_chars`(기본 40000) 이하면 Map 을 건너뛰고 raw 전사를 한 번에 요약한다. Map-Reduce 는 부분요약→통합으로 정보를 두 번 깎아 긴 회의가 과도하게 짧아지는 문제(예: 51분→10줄)가 있어서, 폴백 모델 컨텍스트에 통째로 들어가는 길이는 이중압축을 피한다. Slack 봇은 청크가 항상 1개라 사실상 매번 single-shot이다.
- **요약 회복탄력성**: 모델 폴백 체인(`summarize.models`)을 위에서부터 시도, 모델당 429/타임아웃 시 지수 백오프 재시도. Map 단계는 `max_chunk_failure_pct` 만큼 부분실패를 허용한다(0 이면 청크 하나라도 실패 시 전체 실패). legacy 전용.
- **전사 캐시(issue #5, legacy 전용)**: `legacy/cache.py` 가 원본 오디오 SHA-256 키로 전사 결과를 `data/transcripts` 에 보관 → 요약 등 후속 단계 실패 후 재처리 시 가장 비싼 전사를 건너뛴다. **캐시 실패는 절대 전사를 막지 않는다**(정확성 무관 최적화라 캐시 없이 폴백). Slack 봇은 이 캐시를 쓰지 않는다.
- **RTF ETA 자동보정(legacy 전용)**: `legacy/rtf_calibration.py` 가 실측 RTF 를 EMA 로 `.rtf_state` 에 누적해 다음 전사 ETA 를 하드웨어에 수렴시킨다. yaml 의 `rtf_estimate` 는 콜드스타트 초기값일 뿐.
- **신뢰도 게이트 / 완전성 검사(legacy 전용)**: `legacy/transcribe.py` 가 `no_speech_prob`/`avg_logprob` 로 무음·환각을 거르고, 오디오 길이 대비 마지막 세그먼트 end 가 `completeness_tolerance_sec` 를 넘게 잘리면 실패시킨다. OpenRouter STT(Slack 봇)는 이런 게이트가 없다 — 응답 text 가 비어있을 때만 실패 처리.

### legacy 파이프라인 (`legacy/`, 로컬 whisper.cpp)

```
legacy/audio.py           ffmpeg 로 16kHz mono WAV 변환
legacy/transcribe.py      whisper-cli subprocess 전사 → 파싱 → 신뢰도 게이트 + 완전성 검사
legacy/chunking.py        세그먼트를 시간창(15분)+오버랩(30초)으로 분할(Chunk 는 core 공유)
legacy/cache.py           전사 결과 캐시
legacy/rtf_calibration.py 전사 소요시간 RTF 자동보정
legacy/pipeline.py        위 단계 오케스트레이션(run_pipeline) + core summarize/report 재사용
legacy/watcher.py         data/inbox 폴링 데몬
legacy/run_pipeline.py    단발 CLI 엔트리포인트
legacy/run_watcher.py     watcher 데몬 엔트리포인트
```

`scripts/run_pipeline.py`/`scripts/run_watcher.py` 는 더 이상 없다 — 각각 `legacy/run_pipeline.py`/`legacy/run_watcher.py` 로 이동했다. `deploy/install-launchd.sh`(launchd LaunchAgent), `Containerfile`/`compose.yaml`(podman/docker) 은 모두 `legacy/run_watcher.py` 를 가리키도록 갱신돼 있다. 자세한 배경·재실행 방법은 `legacy/README.md` 참고.

### 예외 계층

`src/exceptions.py` 에 `PipelineError` 루트 아래 `DependencyError`/`CacheError`/`SlackBotError`(Slack 봇 전용) 등 단계별 예외가 있다. 새 실패 모드는 여기에 추가하고 호출부(`run_pipeline`/`bot.py`)에서 `PipelineError` 로 잡아 사용자 메시지로 변환한다.

## 코딩 컨벤션

`coding_convention.md`(AgileSoda Algorithm Team) 준수: snake_case 함수/변수, PascalCase 클래스, UPPER_SNAKE_CASE 상수, 큰따옴표 문자열, 4 spaces, line-length 120. 함수 시그니처에 타입힌트 필수, 설정 객체는 `@dataclass(frozen=True)`(불변).

LLM 프롬프트는 코드가 아니라 `prompts/map_summary.txt`·`prompts/reduce_summary.txt` 텍스트 파일로 분리돼 있다(Slack 봇/legacy 공용). 프롬프트 튜닝은 이 파일에서.
