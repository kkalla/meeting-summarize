# 해야 할 일 (Slack 봇 전환 이후)

이 문서는 코드 작업이 끝난 뒤 **사람이 직접 해야 하는 일**만 정리한다. 코드/구조 변경
내역은 `CLAUDE.md`(전체 아키텍처)와 `legacy/README.md`(legacy 파이프라인)에 있다.

## 1. 지금 당장 해야 할 것 (봇을 켜기 전 필수)

- [x] **로컬에서 의존성 재설치**: `uv sync --dev` — `slack-bolt`, `httpx` 가 새로 추가됐다.
- [x] **Slack 앱 생성 + 토큰 발급** — 매니페스트 파싱이 안 먹혀서(YAML 탭에서도 에러) 수동(from scratch)으로 진행. `deploy/slack_app_manifest.yaml` 은 설정값 참고용으로만 남겨둔다.
  - https://api.slack.com/apps → "Create New App" → "From scratch" → 앱 이름/워크스페이스 선택 → Create
  - **Socket Mode**: 좌측 메뉴 "Socket Mode" → 켜기(Enable) → 이때 App-Level Token 생성 팝업이 뜸 → 스코프 `connections:write` 로 토큰 생성(`xapp-`로 시작, 이름은 아무거나 예: `socket-token`)
  - **OAuth & Permissions**: 좌측 메뉴 "OAuth & Permissions" → "Scopes" > "Bot Token Scopes" 에 아래 5개 추가
    - `chat:write`, `files:read`, `files:write`, `im:history`, `im:read`
  - **Event Subscriptions**: 좌측 메뉴 "Event Subscriptions" → 켜기(Enable Events) → "Subscribe to bot events" 에 `message.im` 추가 → Save
  - **설치**: "OAuth & Permissions" 상단 "Install to Workspace" 클릭 → 권한 승인 → Bot User OAuth Token(`xoxb-`로 시작) 확인
  - 위 순서(Socket Mode 먼저) 지키는 게 중요 — Event Subscriptions 는 Socket Mode 가 켜져 있어야 "Request URL" 없이 바로 이벤트 구독이 가능하다.
  - **App Home**: 좌측 메뉴 "App Home" → "Show Tabs" → **Messages Tab** 켜기 → "Allow users to send Slash commands and messages from the messages tab" 체크. 이거 빠지면 DM 창에 "이 앱으로 메시지를 보내는 기능이 꺼져 있습니다"라고 뜨고 메시지 입력 자체가 막힌다(재설치 불필요, 바로 반영).
- [x] **`.env` 채우기**: `SLACK_BOT_TOKEN`, `SLACK_APP_TOKEN` 추가(`.env.example` 참고). `OPENROUTER_API_KEY` 는 기존 값 재사용.
  - `.env.example` 의 placeholder(`sk-or-v1-여기에_실제_키`)를 그대로 복사만 해두면 실제 키로 안 채워진 채 남는다 — 헤더 인코딩에서 `UnicodeEncodeError` 로 터지니, https://openrouter.ai/keys 에서 발급한 진짜 값으로 교체했는지 다시 확인할 것.
- [x] **OpenRouter 크레딧 충전**: https://openrouter.ai/settings/credits — STT(`microsoft/mai-transcribe-1.5`)는 무료 티어가 없다(시간당 약 $0.36). 잔액 0이면 첫 요청부터 402 에러로 실패한다.
- [x] **실행 + 스모크 테스트**: `uv run python scripts/run_slack_bot.py` 실행 후, 봇과의 DM에 짧은(1~2분) 오디오 파일을 올려 전사 → 요약 → `.md` 업로드까지 한 번 끝까지 확인.

## 2. 알려진 미해결 이슈 — 우선순위 높음

- [x] **긴 오디오 502 문제 — 해결**: 46분 실제 회의로 재현 확인 후 원인 특정. 처음엔 "60초 타임아웃" 가설이었으나, 실제로는 `-c copy`(스트림 그대로 복사)로 만든 조각이 원본(아이패드 Voice Memos `qt` 컨테이너)의 이상한 edit list 를 그대로 물려받아 OpenRouter 백엔드가 못 씹고 502(Cloudflare 게이트웨이 에러, "Host: Error")를 낸 것으로 추정.
  - `src/slack_bot/audio_split.py` 추가: ffprobe 로 길이 확인 → `stt.segment_minutes`(15분) 초과 시 ffmpeg 로 겹치는 시간창(오버랩 `stt.segment_overlap_sec`, 30초) 분할. `-c copy` 대신 16kHz mono 64kbps AAC 로 재인코딩(컨테이너 잔재 제거 + 페이로드도 축소).
  - `src/slack_bot/processor.py`: 구간별 전사 → 청크 여러 개로 조립 → 기존 Map-Reduce 요약(`summarize_meeting`) 그대로 재사용.
  - 46분 파일로 2회 연속 성공 확인(각 4구간, 18002자).
- [x] **`.qta`(아이패드 Voice Memos) 확장자 미지원 — 해결**: `configs/slack_bot.yaml` allowed_extensions 추가 + `processor.py` 에서 OpenRouter STT `format` 필드를 `m4a` 로 매핑(컨테이너가 m4a 와 같은 mp4 계열).
- [x] **요약 본문에 LLM reasoning 누출 — 해결**: 무료 모델(`nvidia/nemotron-3-super-120b-a12b:free`)이 `<think>` 태그 없이 평문으로 확인 메모("Now produce final answer...")를 늘어놓다가 지시된 헤더(`## 핵심 요약`)를 중복 출력하는 케이스를 46분 회의 리포트에서 실제로 발견. `src/summarize.py` 에 방어 로직 추가 — 헤더가 2번 이상 나오면 마지막 등장부터만 사용.
- [ ] **중복 이벤트로 인한 이중 처리 — 미해결, 낮은 우선순위**: Socket Mode 연결이 불안정할 때(예: 테더링/불안정한 네트워크) 사용자가 같은 파일을 여러 번 재업로드하면, 각각이 서로 다른 진짜 Slack 이벤트라 현재의 `event_ts` 기반 중복 캐시로는 못 거른다(자동 재전송이 아니라 사용자가 실제로 여러 번 시도한 것이라 판별이 어려움). 실전에서 한 번 발생(같은 46분 파일이 STT 두 번 호출됨 — 요금 이중 발생). 필요해지면 파일 내용 해시 기반으로 짧은 시간 창 내 중복을 추가로 거르는 방안 검토.

## 3. 정리할 것 (부수적, 안 해도 동작엔 지장 없음)

- [x] **`*.isorted` 잔여 파일 삭제**: 재확인 결과 이미 없음(`find . -name "*.isorted"` 빈 결과).
- [x] **legacy launchd 데몬 끄기**: `com.max.meeting-watcher` unload 완료(`launchctl list` 에서 PID `-`/crash loop 상태였던 것 확인 후 종료). podman 컨테이너를 쓰고 있었다면 그것도 `podman ps` 로 확인 후 정리.
- [x] **`configs/pipeline.yaml` 로컬 변경사항 확인**: STT 프롬프트 용어집 보강 건 — `configs: STT 프롬프트 용어집 보강` 커밋으로 반영 완료.
- [x] **git 커밋**: `configs: STT 프롬프트 용어집 보강` + `feat: Slack 봇을 주력 진입점으로 추가...` 두 커밋으로 분리 완료, push 완료.
- [x] **Slack 봇 상시 실행(데몬화)**: `deploy/com.max.meeting-slack-bot.plist` + `deploy/install-launchd-slack-bot.sh` 추가, launchd LaunchAgent 로 등록 완료(로그인 시 자동 시작, 크래시 시 자동 재시작). 로그는 `logs/slack_bot.log`/`logs/slack_bot.err.log`(Python logging 기본이 stderr 라 실제로는 `.err.log` 쪽에 찍힌다). 상태: `launchctl list | grep meeting-slack-bot`. 프로젝트 폴더를 다시 옮기면 plist 안 절대경로 재확인 필요(legacy watcher 때와 동일한 함정).

## 4. 참고 문서

- `CLAUDE.md` — 전체 아키텍처(Slack 봇이 주력, legacy 파이프라인 구조)
- `legacy/README.md` — whisper.cpp 로컬 파이프라인을 여전히 쓰고 싶을 때
- `.env.example` — 필요한 환경변수 전체 목록
- `deploy/slack_app_manifest.yaml` — Slack 앱 생성용 매니페스트(스코프/이벤트/Socket Mode 설정 포함)
