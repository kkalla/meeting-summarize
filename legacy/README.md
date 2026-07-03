# legacy/ — 로컬 whisper.cpp 기반 CLI 파이프라인 (retired)

이 폴더는 Slack 봇(`src/slack_bot/`)이 주력 사용법이 되기 전까지 이 프로젝트의 유일한
인터페이스였던 로컬 STT(whisper.cpp) + OpenRouter 요약 CLI 파이프라인이다. 더 이상
활발히 개발되지는 않지만, 완전히 동작하는 상태로 보존돼 있다 — 필요하면 그대로 실행 가능.

## 왜 legacy 가 됐나

- Slack 봇이 로컬 whisper.cpp 빌드 없이(순수 API 호출만으로) 어디서든 돌아간다.
- OpenRouter STT(`microsoft/mai-transcribe-1.5`)가 실제로는 유료(시간당 약 $0.36)라
  "무료" 이점은 없지만, 대신 Metal 가속 Mac 을 상시 켜둘 필요가 없고 배포가 훨씬 단순하다.
- 요약(LLM) 단계는 그대로 `src/summarize.py`/`src/report.py` 를 공유하므로 로직이
  두 번 유지되지 않는다.

## 여전히 이 파이프라인을 쓰고 싶다면

```bash
./setup.sh                                              # ffmpeg + whisper.cpp(Metal) 빌드 + 모델 다운로드
uv run python legacy/run_pipeline.py 회의.m4a -o out.md  # 단발 실행
uv run python legacy/run_watcher.py                     # inbox 폴링 데몬(launchd 없이)
```

`deploy/install-launchd.sh` 로 launchd 상시 실행, `podman compose up -d --build` 로 컨테이너
실행도 여전히 이 legacy 경로를 가리키도록 갱신돼 있다.

## 구조

```
audio.py          ffmpeg 로 16kHz mono WAV 변환
transcribe.py     whisper-cli subprocess 전사 → 파싱 → 신뢰도 게이트 + 완전성 검사
chunking.py       세그먼트를 시간창(15분)+오버랩(30초)으로 분할 (Chunk 는 src/chunking.py 공유)
cache.py          전사 결과 캐시(원본 오디오 SHA-256 키)
rtf_calibration.py 전사 소요시간 실측 RTF 자동 보정
pipeline.py       위 단계 오케스트레이션 + src.summarize/src.report 재사용
watcher.py        data/inbox 폴링 데몬
run_pipeline.py   단발 CLI 엔트리포인트
run_watcher.py    watcher 데몬 엔트리포인트
```

`src/chunking.py` 의 `Chunk` 데이터클래스와 `src/config.py`/`src/exceptions.py`/
`src/summarize.py`/`src/report.py` 는 core 로 남아 Slack 봇과 공유한다 — 이 폴더로
옮기지 않았다.
