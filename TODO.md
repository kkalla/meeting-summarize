# 해야 할 일 (Slack 봇 전환 이후)

이 문서는 코드 작업이 끝난 뒤 **사람이 직접 해야 하는 일**만 정리한다. 코드/구조 변경
내역은 `CLAUDE.md`(전체 아키텍처)와 `legacy/README.md`(legacy 파이프라인)에 있다.

## 1. 지금 당장 해야 할 것 (봇을 켜기 전 필수)

- [x] **로컬에서 의존성 재설치**: `uv sync --dev` — `slack-bolt`, `httpx` 가 새로 추가됐다.
- [ ] **Slack 앱 생성 + 토큰 발급** — 매니페스트 파싱이 안 먹혀서(YAML 탭에서도 에러) 수동(from scratch)으로 진행. `deploy/slack_app_manifest.yaml` 은 설정값 참고용으로만 남겨둔다.
  - https://api.slack.com/apps → "Create New App" → "From scratch" → 앱 이름/워크스페이스 선택 → Create
  - **Socket Mode**: 좌측 메뉴 "Socket Mode" → 켜기(Enable) → 이때 App-Level Token 생성 팝업이 뜸 → 스코프 `connections:write` 로 토큰 생성(`xapp-`로 시작, 이름은 아무거나 예: `socket-token`)
  - **OAuth & Permissions**: 좌측 메뉴 "OAuth & Permissions" → "Scopes" > "Bot Token Scopes" 에 아래 5개 추가
    - `chat:write`, `files:read`, `files:write`, `im:history`, `im:read`
  - **Event Subscriptions**: 좌측 메뉴 "Event Subscriptions" → 켜기(Enable Events) → "Subscribe to bot events" 에 `message.im` 추가 → Save
  - **설치**: "OAuth & Permissions" 상단 "Install to Workspace" 클릭 → 권한 승인 → Bot User OAuth Token(`xoxb-`로 시작) 확인
  - 위 순서(Socket Mode 먼저) 지키는 게 중요 — Event Subscriptions 는 Socket Mode 가 켜져 있어야 "Request URL" 없이 바로 이벤트 구독이 가능하다.
- [ ] **`.env` 채우기**: `SLACK_BOT_TOKEN`, `SLACK_APP_TOKEN` 추가(`.env.example` 참고). `OPENROUTER_API_KEY` 는 기존 값 재사용.
- [x] **OpenRouter 크레딧 충전**: https://openrouter.ai/settings/credits — STT(`microsoft/mai-transcribe-1.5`)는 무료 티어가 없다(시간당 약 $0.36). 잔액 0이면 첫 요청부터 402 에러로 실패한다.
- [ ] **실행 + 스모크 테스트**: `uv run python scripts/run_slack_bot.py` 실행 후, 봇과의 DM에 짧은(1~2분) 오디오 파일을 올려 전사 → 요약 → `.md` 업로드까지 한 번 끝까지 확인.

## 2. 알려진 미해결 이슈 — 우선순위 높음

- [ ] **긴 오디오 타임아웃 미해결**: OpenRouter STT 문서상 업스트림 프로바이더 타임아웃이 60초다. 지금 코드(`src/slack_bot/openrouter_stt.py`)는 파일 전체를 한 번에 base64 인코딩해서 던지므로, 1시간짜리 회의 등 긴 파일에서 타임아웃 날 위험이 남아있다. `microsoft/mai-transcribe-1.5` 자체는 "1시간을 15초에 처리"한다고 홍보하지만 60초 제한 자체를 면제해준다는 보장은 없다.
  - 실제로 30분~1시간짜리 실제 회의로 먼저 테스트해서 타임아웃이 나는지 확인 필요.
  - 나면: `ffmpeg` 로 오디오를 N분 단위(예: 10~15분, 약간의 오버랩)로 분할 → 구간별 STT 호출 → 텍스트 이어붙이기 방식 도입 필요(legacy 의 `chunk_segments` 시간창 로직과 유사한 아이디어이지만, 이번엔 텍스트가 아니라 오디오 자체를 자르는 전처리 단계).
  - ffmpeg 분할을 넣으면 Slack 봇 실행 환경에 다시 ffmpeg 의존성이 생긴다(whisper.cpp 만큼 무겁진 않음).

## 3. 정리할 것 (부수적, 안 해도 동작엔 지장 없음)

- [ ] **`*.isorted` 잔여 파일 13개 삭제**: 작업 중 isort 가 남긴 임시 파일인데, 이 세션의 샌드박스 권한으로는 삭제가 안 돼 남아있다. Finder 또는 터미널에서 아래 파일들을 지우면 된다(전부 `legacy/`, `tests/legacy/` 안에 있고 `.py` 확장자가 아니라 아무 것도 import/실행하지 않는다 — 지우지 않아도 안전하지만 지저분하다):
    ```
    legacy/__init__.py.isorted
    legacy/audio.py.isorted
    legacy/cache.py.isorted
    legacy/chunking.py.isorted
    legacy/pipeline.py.isorted
    legacy/rtf_calibration.py.isorted
    legacy/run_pipeline.py.isorted
    legacy/run_watcher.py.isorted
    legacy/transcribe.py.isorted
    legacy/watcher.py.isorted
    tests/legacy/__init__.py.isorted
    tests/legacy/test_audio.py.isorted
    tests/legacy/test_rtf_calibration.py.isorted
    ```
- [x] **legacy launchd 데몬 끄기**: `com.max.meeting-watcher` unload 완료(`launchctl list` 에서 PID `-`/crash loop 상태였던 것 확인 후 종료). podman 컨테이너를 쓰고 있었다면 그것도 `podman ps` 로 확인 후 정리.
- [ ] **`configs/pipeline.yaml` 로컬 변경사항 확인**: 이번 작업과 무관하게 `stt.prompt` 필드가 이미 로컬에 수정된 채 커밋 안 된 상태였다(용어집에 항목 추가). 커밋할 변경사항 정리할 때 같이 검토.
- [ ] **git 커밋**: 이번 세션에서 만든 변경은 아직 커밋 안 된 상태다(`git status` 로 확인). 파일 이동이 많아서(`src/*.py` → `legacy/*.py` 등) `git add -A` 후 diff 한 번 훑어보고 커밋 권장.

## 4. 참고 문서

- `CLAUDE.md` — 전체 아키텍처(Slack 봇이 주력, legacy 파이프라인 구조)
- `legacy/README.md` — whisper.cpp 로컬 파이프라인을 여전히 쓰고 싶을 때
- `.env.example` — 필요한 환경변수 전체 목록
- `deploy/slack_app_manifest.yaml` — Slack 앱 생성용 매니페스트(스코프/이벤트/Socket Mode 설정 포함)
