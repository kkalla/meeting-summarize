# Spec: 메뉴바 앱 (watcher 컨트롤러)

> 상태: **Draft (후속 작업)** — 이번 PR 에는 spec 문서만 포함. 구현은 별도 작업.
> 작성일: 2026-06-05

## 1. 배경 / 문제

watcher 를 호스트 launchd LaunchAgent 로 상시 실행하도록 이전했다(이전 PR). 운영하며 두 가지가 걸린다.

1. **식별성** — macOS(Ventura+) "로그인 항목 및 백그라운드 활동" 목록에 watcher 가
   `bash`/`python` 같은 인터프리터 이름으로 뜬다. LaunchAgent 가 서명·번들 식별자
   (`CFBundleName`)가 없는 raw 스크립트라, macOS 가 앱 이름 대신 실행 인터프리터로 표시한다.
2. **토글 번거로움** — 켜고 끄려면 `launchctl load/unload` 명령을 외워 터미널에서 쳐야 한다.

## 2. 목표

- 백그라운드 항목에 **"MeetingWatcher" + 아이콘**으로 명확히 표시된다.
- 메뉴바 아이콘 클릭만으로 **시작/중지 토글, 상태 확인, 로그 열기**가 된다.
- 코드서명/공증 **없이** 동작한다(개인 로컬용, 첫 실행 시 우클릭→열기 1회).

## 3. 비목표 (이번 범위 밖)

- 코드서명 / Apple notarization / 외부 배포.
- whisper.cpp·ffmpeg·모델(.bin)을 `.app` 안에 번들링하는 것 — 이들은 그대로 프로젝트
  경로에서 `.venv` python 이 사용한다. 메뉴바 앱은 **컨트롤러일 뿐** 전사 의존성을 안 가진다.
- watcher 파이프라인 로직 변경(전사/요약/캐시 등).

## 4. 아키텍처: 앱이 watcher 를 직접 관리 (방식 A)

메뉴바 앱이 `scripts/run_watcher.py` 를 **자식 프로세스**로 띄우고 관리한다. launchd 는 제거한다.

```
MeetingWatcher.app (rumps 메뉴바 앱, .venv python)
  └─ subprocess: .venv/bin/python scripts/run_watcher.py -c configs/pipeline.yaml
        └─ (기존 파이프라인: ffmpeg → whisper.cpp(Metal) → OpenRouter)
```

- watcher 가 앱의 **자식**이라 백그라운드 항목 목록엔 **앱 하나만** 깔끔히 뜬다(식별성 해결).
- launchd 의 KeepAlive 자동재시작은 앱이 대체한다: watcher 프로세스 종료(crash)를 감지해
  재시작하되, 짧은 시간 내 반복 crash 면 백오프하고 메뉴에 오류 상태를 표시한다.

### 방식 B(기각): 앱이 launchctl 토글만

메뉴바는 `launchctl` on/off 만 호출하고 watcher 는 launchd 가 관리. 견고하지만 백그라운드
항목에 앱 + watcher 가 **둘 다** 떠 식별성 문제가 남으므로 채택하지 않는다.

## 5. 기능 요구사항

메뉴바 아이콘 + 드롭다운 메뉴:

- **상태 표시**: 아이콘/타이틀로 `● 실행 중` / `○ 중지` / `⚠ 오류(반복 crash)` 구분.
- **Start / Stop** (토글): watcher 자식 프로세스 시작/정상 종료(SIGTERM, graceful).
- **Restart**: 중지 후 재시작.
- **로그 열기**: `logs/watcher.log` 를 Console.app 또는 기본 편집기로 연다.
- **inbox 열기**: `data/inbox` 를 Finder 로 연다(파일 떨구기 편의).
- **로그인 시 자동 시작** (체크 토글): macOS 로그인 항목 등록/해제(`SMAppService` 또는
  `~/Library/LaunchAgents` 에 앱 실행용 plist).
- **Quit**: 앱 종료 시 watcher 자식도 graceful 종료.

## 6. 기술 스택

- **rumps** — 메뉴바 앱 프레임워크(pip). `pyproject.toml` 의 선택적 의존성 그룹으로 추가.
- **py2app** — `.app` 번들 생성(`Info.plist` 에 `CFBundleName=MeetingWatcher`, `LSUIElement=1`
  로 Dock 아이콘 숨김). 메뉴바 앱은 의존성이 가벼워 번들링이 단순하다.
- **프로세스 관리** — `subprocess.Popen` + 종료 감지 스레드(또는 rumps timer 폴링).
- watcher 의 `WorkingDirectory`/`PYTHONPATH`/`PATH`(ffmpeg) 는 현재 plist 와 동일하게 앱이 주입.

## 7. 마이그레이션

- 기존 `deploy/com.max.meeting-watcher.plist` + `install-launchd.sh` 는 메뉴바 앱으로 대체하고
  제거(또는 "headless 대안"으로 문서에 남김).
- README 4번 섹션을 메뉴바 앱 설치/사용으로 갱신.
- 전사 RTF 자동 보정(`data/transcripts/.rtf_state`)은 그대로 동작 — 변경 없음.

## 8. 작업량 추정

대략 **하루치**: 메뉴바 앱 로직(토글/상태/로그/자동시작), watcher 프로세스 관리 + crash 백오프
재시작, 아이콘 에셋, py2app 번들 설정. 코드서명/공증 없음, 추가 비용 없음.

## 9. 오픈 이슈 / 결정 필요

- 로그인 자동 시작 구현: 최신 `SMAppService`(macOS 13+) vs LaunchAgent plist — 서명 없는
  앱에서 어느 쪽이 백그라운드 항목에 더 깔끔히 뜨는지 구현 시 확인.
- 아이콘 디자인(상태별 변형 필요 여부).
- crash 백오프 임계값(예: 60초 내 3회 이상이면 자동재시작 중단하고 오류 상태).
