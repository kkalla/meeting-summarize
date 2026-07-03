"""파이프라인 전역에서 쓰는 custom 예외 계층.

모든 예외는 :class:`PipelineError` 를 상속한다. CLI 진입점은 이 베이스만
잡아서 사용자 친화적 메시지를 출력할 수 있다.
"""

from __future__ import annotations


class PipelineError(Exception):
    """파이프라인에서 발생하는 모든 도메인 예외의 베이스."""


class DependencyError(PipelineError):
    """외부 바이너리(ffmpeg/whisper-cli) 미설치 또는 모델/키 누락."""


class TranscriptionError(PipelineError):
    """전사 실패: whisper-cli 비정상 종료, JSON 파싱 실패, 잘린/불완전 전사 등."""


class SummarizationError(PipelineError):
    """요약 실패: OpenRouter 호출이 재시도·폴백을 모두 소진했거나 허용 누락률 초과."""


class ReasoningLeakError(SummarizationError):
    """reasoning 모델이 본문 없이 사고과정(``<think>``)만 누출한 경우.

    provider 가 ``reasoning.exclude`` 를 무시해 content 에 사고과정만 남긴 상황으로,
    같은 모델로 재시도해도 결정적으로 동일하다. 재시도를 건너뛰고 다음 모델로 폴백한다.
    """


class TruncatedResponseError(SummarizationError):
    """응답이 ``max_tokens`` 에서 잘린 경우(``finish_reason="length"``).

    reasoning 모델이 사고과정에서 토큰을 소진해 본문을 못 낸 상황으로, 같은 모델·같은
    예산으로 재시도해도 결정적으로 동일하게 잘린다. 재시도를 건너뛰고 다음 모델로 폴백한다.
    """


class CacheError(PipelineError):
    """전사 캐시 조회/저장 실패. 치명적이지 않게 다룬다(캐시 없이 전사로 폴백)."""


class SlackBotError(PipelineError):
    """Slack 봇 전용 실패: 파일 다운로드, Slack API 호출 등 STT/요약 외 단계."""
