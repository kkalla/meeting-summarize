"""OpenRouter Map-Reduce 요약: 429 지수 백오프 재시도 + 모델 폴백 체인.

OpenRouter 는 OpenAI SDK 드롭인이다. 청크가 하나거나 전체가 컨텍스트 임계 이하면
Map 을 생략하고 single-shot 으로 요약한다. Map 일부 실패는 누락률 임계로 정책 분기한다.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass

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
from src.exceptions import (
    ReasoningLeakError,
    SummarizationError,
    TruncatedResponseError,
)

logger = logging.getLogger(__name__)

PCT_FULL = 100.0


@dataclass(frozen=True)
class SummaryResult:
    """요약 결과. 본문과 최종 본문을 생성한 모델명을 함께 담는다.

    Attributes:
        body: 3섹션 Markdown 요약 본문.
        model: 최종 본문(single-shot 또는 reduce)을 성공적으로 생성한 모델명.
            폴백 체인에서 실제로 채택된 모델이라 config 의 첫 모델과 다를 수 있다.
    """

    body: str
    model: str


# OpenAI chat completions 의 finish_reason 값. max_tokens 소진으로 본문이 잘린 경우.
_FINISH_REASON_LENGTH = "length"

# 일부 reasoning 모델은 OpenRouter reasoning.exclude 를 무시하고 content 에 사고과정을
# <think>...</think> 로 인라인으로 남긴다. 본문에서 방어적으로 제거한다.
# 닫는 태그가 없으면(`</think>|$`) <think> 이후 문자열 끝까지 제거한다 — 사고과정이 어디서
# 끝나는지 신호가 없으므로 뒤따르는 텍스트도 사고과정으로 간주한다. 따라서 모델이 <think> 를
# 닫지 않은 채 본문을 이어붙이면 그 본문도 함께 사라진다(→ 빈 결과 → ReasoningLeakError 폴백).
# 이는 의도된 트레이드오프다: 복구 불가능한 누출을 조용히 새게 두느니 시끄럽게 폴백시킨다.
_THINK_BLOCK = re.compile(r"<think>.*?(?:</think>|$)", re.DOTALL | re.IGNORECASE)
# 블록 제거 후에도 남는 고아 </think>. 중첩 <think> 가 안쪽 </think> 에서 끊겨 바깥 </think>
# 가 남거나, provider 가 여는 태그 없이 닫는 태그만 흘린 비정상 누출의 흔적이다.
_ORPHAN_THINK_CLOSE = re.compile(r"</think>", re.IGNORECASE)

# <think> 태그 없이도 누출되는 변종: 일부(특히 무료) 모델이 지시를 확인/재서술하는 평문
# 메모("Now produce final answer... Let's craft")를 늘어놓다가 reduce_summary.txt 가 요구하는
# 첫 헤더로 다시 시작한다. 헤더가 두 번 이상 등장하면 마지막 등장이 실제 최종 출력이므로 그
# 앞은 버린다 — 모델이 스스로 표시한 "진짜 시작점"을 신뢰하는 방어적 자르기.
_REQUIRED_LEADING_HEADER = "## 핵심 요약"


def _strip_leaked_preamble(text: str, *, model: str) -> str:
    """``_REQUIRED_LEADING_HEADER`` 가 두 번 이상 나오면 마지막 등장부터만 남긴다."""
    first_index = text.find(_REQUIRED_LEADING_HEADER)
    last_index = text.rfind(_REQUIRED_LEADING_HEADER)
    if last_index > first_index:
        logger.warning("모델 %s 응답에 헤더가 중복 등장(사전 메모 누출 추정) — 마지막 등장부터만 사용합니다.", model)
        return text[last_index:].strip()
    return text


def _strip_reasoning(text: str) -> str:
    """content 에 인라인으로 남은 ``<think>...</think>`` 블록을 제거한다.

    닫는 태그가 없는 누출(<think> 가 열린 뒤 닫히지 않음)은 사고과정 뒤에 본문이
    이어지더라도 문자열 끝까지 전부 제거한다 — 사고/본문 경계 신호가 없기 때문이다.

    블록을 제거하고도 고아 ``</think>`` 가 남으면 중첩 등 비정상 누출 신호다. 잔여 사고과정과
    본문의 경계를 신뢰성 있게 가를 수 없으므로(섣불리 자르면 멀쩡한 본문이 유실된다) 빈 문자열을
    반환해 호출부가 누출로 보고 다음 모델로 폴백하게 한다 — 조용히 새거나 잘리느니 시끄럽게 폴백.
    """
    without_blocks = _THINK_BLOCK.sub("", text)
    if _ORPHAN_THINK_CLOSE.search(without_blocks):
        return ""
    return without_blocks.strip()


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
) -> SummaryResult:
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
        본문과 최종 본문을 생성한 모델명을 담은 :class:`SummaryResult`.

    Raises:
        SummarizationError: 청크가 비었거나, 누락률 초과, 또는 모든 모델 실패.
    """
    if not chunks:
        raise SummarizationError("요약할 청크가 없습니다.")

    llm = client or OpenAI(base_url=config.base_url, api_key=api_key)

    def complete(prompt: str) -> tuple[str, str]:
        return _complete_with_fallback(prompt, config=config, client=llm, sleep_fn=sleep_fn)

    total_chars = sum(len(chunk.text) for chunk in chunks)
    if len(chunks) <= 1 or total_chars <= single_shot_max_chars:
        logger.info("single-shot 요약 분기 (청크 %d개, %d자)", len(chunks), total_chars)
        full_text = "\n".join(chunk.text for chunk in chunks)
        body, model = complete(reduce_template.format(partial_summaries=full_text))
        return SummaryResult(body=body, model=model)

    partials = _run_map(chunks, map_template, complete, config)
    reduce_input = _build_reduce_input(chunks, partials)
    logger.info("reduce 통합 요약 시작 (부분요약 %d개)", len(partials))
    # 최종 본문을 만드는 건 reduce 단계이므로, 리포트에 남길 대표 모델은 reduce 가 채택한 모델이다
    # (Map 단계는 청크별로 다른 모델로 폴백했을 수 있으나 최종 통합본은 reduce 가 생성).
    body, model = complete(reduce_template.format(partial_summaries=reduce_input))
    return SummaryResult(body=body, model=model)


def _run_map(
    chunks: list[Chunk],
    map_template: str,
    complete: Callable[[str], tuple[str, str]],
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
            # complete 는 (본문, 모델) 을 주지만 Map 단계는 본문만 모은다 — 대표 모델은
            # 최종 통합본을 만드는 reduce 호출에서 정한다.
            partials[chunk.index] = complete(map_template.format(chunk_text=chunk.text))[0]
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
) -> tuple[str, str]:
    """폴백 체인을 순회하며 각 모델에서 지수 백오프 재시도로 LLM 을 호출한다.

    Returns:
        ``(본문, 성공한 모델명)`` 튜플.

    Raises:
        SummarizationError: 모든 모델/재시도를 소진했을 때.
    """
    last_error: Exception | None = None
    for model in config.models:
        for attempt in range(config.max_retries):
            try:
                # _chat_once 는 항상 non-empty 로 strip 된 본문을 반환한다(빈 응답/None/누출은
                # 내부에서 예외로 변환). 따라서 여기서 빈 응답을 다시 검사할 필요가 없다.
                return _chat_once(prompt, model=model, config=config, client=client), model
            except _FATAL_ERRORS as exc:
                raise SummarizationError(f"인증/권한 오류로 요약을 중단합니다(키를 확인하세요): {exc}") from exc
            except (ReasoningLeakError, TruncatedResponseError) as exc:
                # 사고과정 누출·잘림은 같은 모델·같은 예산으로 재시도해도 결정적으로 동일하다
                # → 재시도 낭비 없이 다음 모델로.
                last_error = exc
                logger.warning("모델 %s 결정적 실패 — 재시도 생략, 다음 모델로: %s", model, exc)
                break
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
    ) from last_error


def _chat_once(prompt: str, *, model: str, config: SummarizeConfig, client: OpenAI) -> str:
    """단일 chat completion 호출. 항상 non-empty 로 strip 된 본문을 반환한다.

    빈 응답·content=None·잘림(finish_reason=length)·사고과정만 누출된 경우는 모두
    반환 대신 예외(SummarizationError/ReasoningLeakError/TruncatedResponseError)로 변환된다.
    """
    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=config.max_tokens,
        temperature=config.temperature,
        timeout=config.request_timeout_sec,
        # reasoning 모델이 사고과정(<think>)으로 토큰을 소진해 본문을 못 내는 것을 막는다.
        # effort=low 로 사고량을 줄이고, exclude=true 로 사고 텍스트 제거를 요청한다(OpenRouter 확장).
        # exclude 는 보장이 아니라 best-effort 힌트라 일부 provider 는 무시한다 — 그래서 본문에
        # 남는 <think> 누출은 _strip_reasoning 으로 한 번 더 방어한다.
        extra_body={"reasoning": {"effort": "low", "exclude": True}},
    )
    if not resp.choices:
        raise SummarizationError(f"{model} 응답에 choices 가 없습니다(콘텐츠 필터/쿼터 가능).")

    choice = resp.choices[0]
    finish_reason = getattr(choice, "finish_reason", None)
    content = choice.message.content
    # content=None 은 빈 문자열과 다른 신호다(콘텐츠 필터/툴콜 등). 사유를 남겨 진단을 돕는다.
    if content is None:
        raise SummarizationError(
            f"{model} 이 본문 없이 응답했습니다(content=None, finish_reason={finish_reason}). "
            "콘텐츠 필터 또는 비정상 종료 가능."
        )
    # 사고과정이 max_tokens 를 소진해 본문이 잘린 경우. 잘린 사고과정을 요약으로 저장하면 안 되므로
    # 결정적 실패로 보고 재시도 없이 다음 모델로 폴백한다(reasoning 모델에서 흔함).
    if finish_reason == _FINISH_REASON_LENGTH:
        raise TruncatedResponseError(
            f"{model} 응답이 max_tokens({config.max_tokens})에서 잘렸습니다"
            "(reasoning 모델이 사고과정에서 토큰을 소진했을 가능성). "
            "configs/pipeline.yaml 의 summarize.max_tokens 를 늘리거나 비-reasoning 모델을 쓰세요."
        )
    stripped = _strip_reasoning(content)
    if not stripped:
        # content 에 <think> 또는 </think> 흔적이 있었으면 reasoning 누출이다(exclude 무시 또는
        # 중첩·비정상 태그). 같은 모델로 재시도해도 결정적으로 동일하므로 재시도 없이 다음 모델로.
        lowered = content.lower()
        if "<think>" in lowered or "</think>" in lowered:
            raise ReasoningLeakError(
                f"{model} 이 본문 없이 사고과정(<think>) 누출만 반환했습니다(finish_reason={finish_reason}). "
                "provider 가 reasoning.exclude 를 무시했을 가능성 — 재시도 없이 다음 모델로 폴백합니다."
            )
        # 태그 흔적도 없이 빈/공백 응답인 경우는 일시적일 수 있으므로(콘텐츠 필터 미설정·순간 장애 등)
        # 누출로 오분류하지 않고 일반 오류로 던져 재시도 경로를 태운다.
        raise SummarizationError(f"{model} 이 빈 응답을 반환했습니다(finish_reason={finish_reason}).")
    return _strip_leaked_preamble(stripped, model=model)
