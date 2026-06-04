"""OpenRouter Map-Reduce 요약: 429 지수 백오프 재시도 + 모델 폴백 체인.

OpenRouter 는 OpenAI SDK 드롭인이다. 청크가 하나거나 전체가 컨텍스트 임계 이하면
Map 을 생략하고 single-shot 으로 요약한다. Map 일부 실패는 누락률 임계로 정책 분기한다.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

from openai import (
    APIError,
    AuthenticationError,
    BadRequestError,
    NotFoundError,
    OpenAI,
    PermissionDeniedError,
)

from src.chunking import Chunk
from src.config import SummarizeConfig
from src.exceptions import SummarizationError

logger = logging.getLogger(__name__)

PCT_FULL = 100.0

# 키/권한 문제 — 어떤 모델로 바꿔도 동일하게 실패하므로 즉시 중단한다.
_FATAL_ERRORS = (AuthenticationError, PermissionDeniedError)
# 요청/모델 자체 문제(잘못된 모델명 등) — 재시도는 무의미하니 바로 다음 모델로 넘어간다.
_SKIP_MODEL_ERRORS = (BadRequestError, NotFoundError)


def summarize_meeting(
    chunks: list[Chunk],
    *,
    config: SummarizeConfig,
    api_key: str,
    map_template: str,
    reduce_template: str,
    single_shot_max_chars: int,
    client: OpenAI | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> str:
    """청크 리스트를 통합 요약(Markdown) 으로 변환한다.

    Args:
        chunks: 요약할 청크들.
        config: 요약 모델/재시도/임계 설정.
        api_key: OpenRouter API 키.
        map_template: ``{chunk_text}`` 플레이스홀더를 가진 Map 프롬프트.
        reduce_template: ``{partial_summaries}`` 플레이스홀더를 가진 Reduce 프롬프트.
        single_shot_max_chars: 이 글자 수 이하면 Map 생략하고 single-shot.
        client: 주입용 OpenAI 클라이언트(테스트). None 이면 내부 생성.
        sleep_fn: 백오프 대기 함수(테스트에서 no-op 주입).

    Returns:
        3섹션 Markdown 요약 본문.

    Raises:
        SummarizationError: 청크가 비었거나, 누락률 초과, 또는 모든 모델 실패.
    """
    if not chunks:
        raise SummarizationError("요약할 청크가 없습니다.")

    llm = client or OpenAI(base_url=config.base_url, api_key=api_key)

    def complete(prompt: str) -> str:
        return _complete_with_fallback(prompt, config=config, client=llm, sleep_fn=sleep_fn)

    total_chars = sum(len(chunk.text) for chunk in chunks)
    if len(chunks) <= 1 or total_chars <= single_shot_max_chars:
        logger.info("single-shot 요약 분기 (청크 %d개, %d자)", len(chunks), total_chars)
        full_text = "\n".join(chunk.text for chunk in chunks)
        return complete(reduce_template.format(partial_summaries=full_text))

    partials = _run_map(chunks, map_template, complete, config)
    reduce_input = _build_reduce_input(chunks, partials)
    logger.info("reduce 통합 요약 시작 (부분요약 %d개)", len(partials))
    return complete(reduce_template.format(partial_summaries=reduce_input))


def _run_map(
    chunks: list[Chunk],
    map_template: str,
    complete: Callable[[str], str],
    config: SummarizeConfig,
) -> dict[int, str]:
    """청크별 부분요약을 생성한다(메모리 캐시). 누락률 초과 시 전체 실패.

    Returns:
        ``{chunk.index: 부분요약}`` 매핑(성공분만).

    Raises:
        SummarizationError: 실패 청크 비율이 ``max_chunk_failure_pct`` 를 초과할 때.
    """
    partials: dict[int, str] = {}
    failed: list[int] = []
    for chunk in chunks:
        try:
            partials[chunk.index] = complete(map_template.format(chunk_text=chunk.text))
            logger.info("청크 %d/%d 부분요약 완료", chunk.index + 1, len(chunks))
        except SummarizationError as exc:
            logger.warning("청크 %d 부분요약 실패: %s", chunk.index + 1, exc)
            failed.append(chunk.index)

    failure_pct = len(failed) / len(chunks) * PCT_FULL
    if failure_pct > config.max_chunk_failure_pct:
        raise SummarizationError(
            f"Map 누락률 {failure_pct:.0f}% 가 허용 임계 {config.max_chunk_failure_pct:.0f}% 를 "
            f"초과했습니다(실패 청크: {[i + 1 for i in failed]})."
        )
    return partials


def _build_reduce_input(chunks: list[Chunk], partials: dict[int, str]) -> str:
    """부분요약들을 Reduce 입력 문자열로 조립한다(누락 청크는 명시)."""
    parts: list[str] = []
    for chunk in chunks:
        label = f"[구간 {chunk.index + 1}]"
        if chunk.index in partials:
            parts.append(f"{label}\n{partials[chunk.index]}")
        else:
            parts.append(f"{label} (요약 실패 — 누락된 구간)")
    return "\n\n".join(parts)


def _complete_with_fallback(
    prompt: str,
    *,
    config: SummarizeConfig,
    client: OpenAI,
    sleep_fn: Callable[[float], None],
) -> str:
    """폴백 체인을 순회하며 각 모델에서 지수 백오프 재시도로 LLM 을 호출한다.

    Raises:
        SummarizationError: 모든 모델/재시도를 소진했을 때.
    """
    last_error: Exception | None = None
    for model in config.models:
        for attempt in range(config.max_retries):
            try:
                content = _chat_once(prompt, model=model, config=config, client=client)
                if not content.strip():
                    raise SummarizationError(f"{model} 이 빈 응답을 반환했습니다.")
                return content.strip()
            except _FATAL_ERRORS as exc:
                raise SummarizationError(f"인증/권한 오류로 요약을 중단합니다(키를 확인하세요): {exc}") from exc
            except _SKIP_MODEL_ERRORS as exc:
                last_error = exc
                logger.warning("모델 %s 비재시도성 오류 — 재시도 생략, 다음 모델로: %s", model, exc)
                break
            except Exception as exc:  # noqa: BLE001 - 일시 오류(429/타임아웃/5xx 등) 재시도/폴백 위해 광범위하게 잡음
                last_error = exc
                # APIError(RateLimit/Timeout/Connection/InternalServer 등) 는 예상된 일시 오류라
                # 메시지만, 그 외(AttributeError/TypeError 등 코딩 버그 가능성)는 스택트레이스를 남긴다.
                if isinstance(exc, APIError):
                    logger.warning("모델 %s 호출 실패 (시도 %d/%d): %s", model, attempt + 1, config.max_retries, exc)
                else:
                    logger.exception(
                        "모델 %s 호출 중 예상치 못한 예외 (시도 %d/%d) — 버그 가능성",
                        model,
                        attempt + 1,
                        config.max_retries,
                    )
                if attempt < config.max_retries - 1:
                    sleep_fn(config.backoff_base**attempt)
        logger.warning("모델 %s 폴백 — 다음 모델로 전환", model)

    raise SummarizationError(
        f"모든 요약 모델 호출에 실패했습니다(시도 모델: {list(config.models)}). 마지막 오류: {last_error}"
    )


def _chat_once(prompt: str, *, model: str, config: SummarizeConfig, client: OpenAI) -> str:
    """단일 chat completion 호출. 응답 본문 문자열을 반환한다."""
    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=config.max_tokens,
        temperature=config.temperature,
        timeout=config.request_timeout_sec,
    )
    if not resp.choices:
        raise SummarizationError(f"{model} 응답에 choices 가 없습니다(콘텐츠 필터/쿼터 가능).")

    choice = resp.choices[0]
    content = choice.message.content
    # content=None 은 빈 문자열과 다른 신호다(콘텐츠 필터/툴콜 등). 사유를 남겨 진단을 돕는다.
    if content is None:
        raise SummarizationError(
            f"{model} 이 본문 없이 응답했습니다(content=None, finish_reason={choice.finish_reason}). "
            "콘텐츠 필터 또는 비정상 종료 가능."
        )
    return content
