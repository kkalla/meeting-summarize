---
stepsCompleted: [1, 2, 3]
inputDocuments: []
workflowType: 'research'
lastStep: 3
status: 'complete'
research_type: 'technical'
research_topic: '회의 녹음 요약 서비스(개인용) 기술 스택 검증'
research_goals: '(1) 로컬 STT 옵션 비교 - faster-whisper vs whisper.cpp vs OpenAI Whisper API, (2) OpenRouter 무료티어 모델로 회의 요약 생성 가능성/품질/제약, (3) 기술 선택 근거 정리'
user_name: 'Max'
date: '2026-06-04'
web_research_enabled: true
source_verification: true
---

# Research Report: technical

**Date:** 2026-06-04
**Author:** Max
**Research Type:** technical

---

## Research Overview

개인용 회의 녹음 요약 서비스의 기술 스택을 검증한 기술 리서치. 로컬 STT 옵션(whisper.cpp / faster-whisper / Whisper API) 비교, OpenRouter 무료티어 LLM의 요약 적합성, 그리고 단일 CLI 파이프라인 통합 방식을 현재 웹 소스 검증과 함께 정리했다. 결론적으로 **로컬 STT는 whisper.cpp, 요약은 OpenRouter 무료티어, 아키텍처는 단일 CLI 파이프라인(Python)**으로 결정.

---

## Technical Research Scope Confirmation

**Research Topic:** 회의 녹음 요약 서비스(개인용) 기술 스택 검증
**Research Goals:** (1) 로컬 STT 옵션 비교 - faster-whisper vs whisper.cpp vs OpenAI Whisper API, (2) OpenRouter 무료티어 모델로 회의 요약 생성 가능성/품질/제약, (3) 기술 선택 근거 정리

**Technical Research Scope:**

- STT 옵션 비교 - faster-whisper / whisper.cpp / Whisper API의 정확도·속도·하드웨어 요구·비용·설치 난이도
- OpenRouter 무료티어 - 무료 모델 목록, 컨텍스트 길이, rate limit, 데이터 정책, 요약 품질
- 아키텍처/플로우 - 녹음 → STT → 청킹 → 요약 파이프라인, 긴 회의 처리 전략
- Integration Patterns - 로컬 STT 출력 → OpenRouter API 연동
- 실무 제약 - 무료티어 한계 및 대안, 비용 발생 지점

**Research Methodology:**

- Current web data with rigorous source verification
- Multi-source validation for critical technical claims
- Confidence level framework for uncertain information
- Comprehensive technical coverage with architecture-specific insights

**Scope Confirmed:** 2026-06-04

---

## Technology Stack Analysis

### STT (Speech-to-Text) — 로컬 옵션 비교

회의 녹음을 텍스트로 바꾸는 핵심 단계. 맥(Apple Silicon) 개인 환경 기준으로 정리.

| 옵션 | 속도(Apple Silicon) | 정확도 | 설치/운영 | 비용 | 비고 |
|------|------|------|------|------|------|
| **whisper.cpp** | large-v3 기준 ~10× 실시간 (Metal 가속), 콜드 부팅 <300ms | 동일 모델이면 동급 | C++ 네이티브, Python 불필요, 단순 | 무료(로컬) | **Apple Silicon 최적** |
| **faster-whisper** | Apple Silicon에선 CPU-only ~3× 실시간, 로딩 ~1.8s | 동일 모델이면 동급 | Python 생태계(CTranslate2), 파이프라인 연동 쉬움 | 무료(로컬) | NVIDIA GPU에서 진가 |
| **OpenAI Whisper API** | 클라우드(로컬 부하 0) | 높음 | API 호출만, 가장 간단 | $0.006/분 (gpt-4o-mini-transcribe는 절반) | 음성이 외부로 나감 |

**핵심 인사이트:**
- 같은 Whisper 모델이면 **전사 품질은 세 옵션 모두 동급**. 차이는 속도/자원/프라이버시.
- Apple Silicon 맥에서는 **whisper.cpp가 약 3배 빠름** (Metal/CoreML 가속, Python 의존성 없음). faster-whisper는 맥에서 CPU-only라 손해.
- faster-whisper는 **Python 파이프라인 통합이 쉬운 게 강점** — 요약 로직까지 한 언어로 묶기 편함.
- Whisper API는 분당 $0.006로 저렴하지만, **개인 회의 = 프라이버시 민감 + "로컬 STT" 목표**와 어긋남. 본 프로젝트 목표상 로컬 2종 중 선택.
- 모델 선택: `large-v3-turbo`가 v3 대비 맥에서 ~5× 빠르면서 품질 유지 → 한국어 회의면 large 계열 권장.

_Sources: [whisper.cpp vs faster-whisper 2026 benchmarks (promptquorum)](https://www.promptquorum.com/power-local-llm/local-whisper-stt-comparison-2026), [Apple Silicon Whisper performance (voicci)](https://www.voicci.com/blog/apple-silicon-whisper-performance.html), [mac-whisper-speedtest (GitHub)](https://github.com/anvanvan/mac-whisper-speedtest), [Whisper Large V3 Turbo benchmark](https://whispernotes.app/blog/introducing-whisper-large-v3-turbo), [Whisper API pricing 2026 (tokenmix)](https://tokenmix.ai/blog/whisper-api-pricing)_

### 요약 LLM — OpenRouter 무료티어

**Rate limit (무료 모델):**
- 크레딧 없으면: **하루 50 요청 / 분당 20 요청**
- $10 크레딧 1회 충전 시: **하루 1,000 요청**으로 상향
- 피크 시간엔 크레딧 있어도 provider 단에서 `429 Too Many Requests` 날 수 있음

**컨텍스트 길이:**
- Free Models Router(`openrouter/free`): **200K 토큰** 컨텍스트
- 개별 무료 모델: Gemini 2.0 Flash 계열은 **~1M 토큰** 장문 처리 가능

**요약에 쓸만한 무료 모델 (2026 기준):**
- **Google Gemini 2.0 Flash (free)** — ~1M 컨텍스트, 장문 회의록 통째로 넣기 좋음
- **DeepSeek Chat v3 / R1** — 추론·요약 품질 우수, R1은 o1급 추론
- **Llama 3.3 70B / Llama 4 Maverick** — 범용 워크호스
- **Qwen3 235b** — 무료티어에서 실사용 가능 수준

**프라이버시 (회의 = 민감 데이터, 중요!):**
- OpenRouter는 기본적으로 **프롬프트/응답 저장 안 함** (메타데이터만 로깅). 명시적 opt-in 해야 로깅됨.
- "providers may train on data" provider로의 라우팅을 **계정 설정에서 opt-out 가능**.
- 단, **무료 라우트는 provider마다 정책이 다름** → 민감 데이터는 주의. 필요시 **ZDR(Zero Data Retention)** 설정을 per-request로 강제 가능.
- 회사 기밀 회의라면 무료티어보다 로컬 LLM(Ollama 등) 또는 유료+ZDR 고려.

_Sources: [OpenRouter Rate Limits](https://openrouter.zendesk.com/hc/en-us/articles/39501163636379-OpenRouter-Rate-Limits-What-You-Need-to-Know), [OpenRouter Free Models (costgoat)](https://costgoat.com/pricing/openrouter-free-models), [Best Free Models on OpenRouter 2026 (TeamDay)](https://www.teamday.ai/blog/best-free-ai-models-openrouter-2026), [OpenRouter Provider Logging docs](https://openrouter.ai/docs/guides/privacy/provider-logging), [Zero Data Retention docs](https://openrouter.ai/docs/guides/features/zdr)_

### 긴 회의 처리 — 청킹 전략

무료티어 200K 컨텍스트라도 다중 시간 회의는 lost-in-the-middle 저하 + 토큰 낭비. 검증된 패턴:
- **Map-Reduce**: 15분 단위로 청킹 → 각 청크 요약 → 최종 프롬프트로 통합 요약
- **청킹 규칙**: 발화 중간에서 자르지 말 것, 화자 전환(speaker turn)을 자연스러운 경계로, 청크 간 **오버랩**으로 액션아이템 누락 방지
- **Divide-and-conquer / 토픽 기반 섹셔닝**으로 장기 의존성 망각 완화

_Sources: [Building LLM pipelines for meeting intelligence (Gladia)](https://www.gladia.io/blog/transcript-to-actionable-notes-llm), [Summarize meetings with LLMs (AssemblyAI)](https://www.assemblyai.com/blog/summarize-meetings-llms-python), [Action-Item-Driven Summarization of Long Meeting Transcripts (arXiv)](https://arxiv.org/pdf/2312.17581)_

### 권장 스택 요약 (개인용)

- **언어/런타임**: Python — STT·요약·청킹 한 파이프라인으로 묶기 유리
- **STT**: whisper.cpp(속도 우선, 맥 최적) 또는 faster-whisper(Python 통합 우선) — 트레이드오프는 [Integration] 섹션에서 확정
- **요약**: OpenRouter 무료티어 (Gemini 2.0 Flash free 또는 DeepSeek 계열) + Map-Reduce 청킹
- **모델**: Whisper large-v3-turbo (한국어 품질·속도 균형)

> **✅ 결정 (2026-06-04):** STT는 **whisper.cpp** 채택 — Apple Silicon 맥에서 속도 우위(Metal 가속, ~3배), Python 의존성 없음. 요약 파이프라인(Python)과는 CLI/서브프로세스 또는 출력 파일 경유로 연동.

## Integration Patterns Analysis

개인용 CLI/스크립트 파이프라인 기준. 엔터프라이즈 패턴(ESB, service mesh 등)은 본 프로젝트 범위 밖이므로 생략.

### 파이프라인 데이터 흐름

```
[녹음 파일 .m4a/.wav]
        │  (필요시 ffmpeg로 16kHz WAV 변환)
        ▼
[whisper.cpp]  ── whisper-cli -m ggml-large-v3-turbo.bin -f audio.wav -oj -ojf out
        │   출력: out.json (segments + timestamps + text)
        ▼
[Python: 전사 로드 + 청킹]  ── JSON 파싱 → 화자/시간 경계로 15분 청크 + 오버랩
        │
        ▼
[OpenRouter API]  ── OpenAI SDK 호환, /api/v1/chat/completions
        │   Map: 청크별 부분 요약 → Reduce: 통합 요약
        ▼
[최종 요약 .md] (액션아이템 / 결정사항 / 핵심논의)
```

### STT 연동 (whisper.cpp)

- **출력 포맷**: txt / srt / vtt / csv / **json** 지원. 파이프라인엔 **JSON(`-oj -ojf <prefix>`)** 권장 — 세그먼트별 timestamp/offset/text 포함이라 청킹·복기에 유리.
- **연동 방식**: whisper.cpp는 C++ 바이너리라 Python에서 **subprocess 호출** 또는 **출력 파일 경유**가 가장 단순. (서버/REST 불필요)
- 입력 전처리: 다양한 녹음 포맷은 **ffmpeg로 16kHz mono WAV** 변환 후 투입이 안정적.

_Sources: [whisper.cpp multi-format output (X-CMD)](https://www.x-cmd.com/pkg/whispercpp/), [Using whisper.cpp JSON output (Mario Chávez)](https://mariochavez.io/desarrollo/2023/12/10/sound-to-script-openia-whisper/), [Whisper output formats (Emory Libraries)](https://guides.libraries.emory.edu/c.php?g=1442123&p=10711508)_

### 요약 API 연동 (OpenRouter)

- **OpenAI SDK 드롭인 호환**: `base_url="https://openrouter.ai/api/v1"` + `api_key=<OPENROUTER_KEY>`로 기존 OpenAI 코드 그대로 사용. 스키마도 OpenAI Chat API와 거의 동일.
  ```python
  from openai import OpenAI
  client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=KEY)
  resp = client.chat.completions.create(
      model="google/gemini-2.0-flash-exp:free",
      messages=[{"role": "user", "content": prompt}],
  )
  summary = resp.choices[0].message.content
  ```
- **모델 교체 용이**: `model` 문자열만 바꾸면 무료↔유료, Gemini↔DeepSeek 전환. 무료 모델은 `:free` suffix.
- **인증/시크릿**: API 키는 **환경변수/`.env`**로 관리 (하드코딩 금지). 개인 프로젝트라도 git 커밋 시 노출 주의.
- **레질리언스**: 무료티어 `429`(rate limit) 대비 → 지수 백오프 재시도 + 모델 폴백(예: free 막히면 다른 free 또는 저가 유료) 권장.

_Sources: [OpenRouter Quickstart](https://openrouter.ai/docs/quickstart), [OpenAI SDK Integration (OpenRouter docs)](https://openrouter.ai/docs/guides/community/openai-sdk), [Create a chat completion (OpenRouter)](https://openrouter.ai/docs/api/api-reference/chat/send-chat-completion-request)_

### 데이터 포맷 / 인터페이스 경계

- STT→요약 사이 계약: **whisper JSON segments** (`{start, end, text}` 배열)이 단일 진실 소스. 청킹 모듈은 이걸 입력으로 받음.
- 요약 출력: Markdown (사람이 읽기 + 재처리 둘 다 무난). 구조는 `## 핵심 요약 / ## 결정사항 / ## 액션아이템(담당·기한)`.

## 최종 기술 선택 근거 (Conclusions)

### 결정 요약

| 영역 | 선택 | 근거 |
|------|------|------|
| **아키텍처** | 단일 CLI 파이프라인 (Python) | 개인용·소규모, 서버/마이크로서비스 불필요. KISS. |
| **STT** | **whisper.cpp** (`large-v3-turbo`) | Apple Silicon에서 Metal 가속으로 ~3배 빠름, Python 의존성 0, 콜드부팅 <300ms. 전사 품질은 어차피 동일 모델이면 동급. |
| **STT 출력** | JSON (`-oj -ojf`) | 세그먼트별 timestamp 포함 → 청킹·복기 용이. |
| **요약 LLM** | **OpenRouter 무료티어** (Gemini 2.0 Flash `:free` 1차, DeepSeek 계열 폴백) | 무료, OpenAI SDK 드롭인, 장문 컨텍스트. |
| **긴 회의 처리** | Map-Reduce 청킹 (15분 + 오버랩, 발화 경계 보존) | lost-in-the-middle 방지, 토큰 절약. |
| **연동** | subprocess(whisper.cpp) + OpenAI SDK(OpenRouter) | 가장 단순, 모델 교체 자유. |

### 핵심 트레이드오프 / 리스크

1. **무료티어 rate limit** — 하루 50요청/분당 20. 긴 회의를 청크로 쪼개면 한 회의에 요청 여러 번 소모 → **하루 처리량 제한**. 대응: $10 1회 충전 시 하루 1,000요청으로 완화, 또는 청크 크기 키워 요청 수 축소.
2. **프라이버시** — 무료 라우트는 provider별 데이터 정책 상이. **민감/기밀 회의는 무료티어 부적합** → 그런 경우 로컬 LLM(Ollama) 또는 유료+ZDR 고려. 일반 개인 회의면 OK(기본 비로깅 + train opt-out).
3. **무료 모델 가용성 변동** — 무료 모델 풀은 수시로 바뀜 → `model` 문자열 설정값으로 빼두고 폴백 체인 구성.
4. **한국어 품질** — Whisper large 계열이면 한국어 전사 양호. 요약 모델도 한국어 무난하나, 프롬프트에 "한국어로 요약" 명시 권장.

### MVP 권장 구현 순서

1. ffmpeg 변환 + whisper.cpp 전사 (JSON 출력) — 로컬에서 텍스트 뽑기까지
2. JSON 파싱 + Map-Reduce 청킹/요약 (OpenRouter 연동, `.env` 키)
3. 최종 Markdown 리포트 출력 (핵심요약/결정사항/액션아이템)
4. 429 백오프·모델 폴백 등 안정화

---

_Research complete: 2026-06-04_
