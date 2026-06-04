# Deferred Work

회의 요약 파이프라인 리뷰(step-04, 2026-06-04)에서 surface된, 이번 스토리 범위 밖이거나 우선순위 낮은 항목들.

## LOW — 전사 완전성 검사가 끝부분 잘림만 탐지
- **출처:** `src/transcribe.py` `check_completeness`
- `audio_duration - segments[-1].end` 만 비교 → 앞부분 누락(첫 세그먼트가 한참 뒤 시작)이나 중간 큰 갭은 못 잡음.
- 개선안: 첫 세그먼트 start 검사 + 인접 세그먼트 간 갭 합산 검사.

## LOW — 청크보다 긴 단일 세그먼트의 중복
- **출처:** `src/chunking.py` `chunk_segments`
- `chunk_len`(15분)보다 긴 단일 세그먼트가 있으면 여러 윈도우에 통째로 중복 포함 → Map 토큰 비용 증가, 청크 start/end가 부정확.
- 현실적으로 whisper.cpp는 세그먼트를 짧게(수~수십 초) 끊어서 거의 발생 안 함. 발생 시 세그먼트를 primary 청크에만 1회 배정하고 이웃엔 오버랩으로만 포함하도록 재설계 필요.

## ENHANCEMENT — 환각 탐지 강화 (게이트 한계)
- **출처:** step-04 edge-case 리뷰
- whisper.cpp는 `no_speech_prob`을 출력하지 않음. `-ojf` 토큰 확률 기반 `avg_logprob` 게이트는 동작하나, "자신만만한 환각"(무음 구간에 높은 확률로 반복 문구 생성)은 logprob이 높아 통과할 수 있음.
- 개선안: 반복 n-gram/동일 문장 반복 비율 휴리스틱을 게이트에 추가.
