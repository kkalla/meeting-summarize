"""OpenRouter STT(``microsoft/mai-transcribe-1.5``) 클라이언트.

로컬 whisper.cpp(``src.transcribe``)와 달리 세그먼트/타임스탬프를 주지 않고 전체
텍스트 하나만 반환한다. 대신 응답의 ``usage.seconds`` 로 오디오 길이를 알 수 있어
리포트 메타데이터(회의 길이)를 별도 프로빙 없이 채울 수 있다.

429/5xx 는 ``summarize.py`` 와 동일한 지수 백오프 패턴으로 재시도한다(이 엔드포인트는
폴백 모델 체인이 없어 모델 하나에 대해서만 재시도한다).
"""

from __future__ import annotations

import base64
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass

import httpx

from src.exceptions import TranscriptionError
from src.slack_bot.config import OpenRouterSttConfig

logger = logging.getLogger(__name__)

# 재시도 대상 HTTP 상태(일시 오류: 쿼터 초과/서버 과부하). 그 외(400/401/404 등)는
# 재시도해도 결정적으로 동일하므로 즉시 실패시킨다.
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


@dataclass(frozen=True)
class SttResult:
    """OpenRouter STT 결과.

    Attributes:
        text: 전사된 텍스트(공백 정리됨).
        duration_sec: 응답 ``usage.seconds`` 로 얻은 오디오 길이(초). 필드가 없으면 0.0.
    """

    text: str
    duration_sec: float


def transcribe_audio(
    audio_bytes: bytes,
    *,
    audio_format: str,
    config: OpenRouterSttConfig,
    api_key: str,
    client: httpx.Client | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> SttResult:
    """오디오 바이트를 OpenRouter STT 로 전사한다.

    Args:
        audio_bytes: 원본 오디오 파일 바이트.
        audio_format: OpenRouter 가 인식하는 포맷 문자열(점 없이, 예: ``"m4a"``, ``"wav"``).
        config: STT 모델/타임아웃/재시도 설정.
        api_key: OpenRouter API 키.
        client: 주입용 httpx 클라이언트(테스트). None 이면 내부에서 생성 후 닫는다.
        sleep_fn: 백오프 대기 함수(테스트에서 no-op 주입).

    Returns:
        전사 텍스트와 오디오 길이를 담은 :class:`SttResult`.

    Raises:
        TranscriptionError: 응답에 유효한 text 가 없거나 재시도를 모두 소진했을 때.
    """
    payload = {
        "model": config.model,
        "input_audio": {
            "data": base64.b64encode(audio_bytes).decode("ascii"),
            "format": audio_format,
        },
    }
    if config.language:
        payload["language"] = config.language

    headers = {"Authorization": f"Bearer {api_key}"}
    url = f"{config.base_url}/audio/transcriptions"

    owns_client = client is None
    http_client = client or httpx.Client(timeout=config.request_timeout_sec)
    try:
        return _post_with_retry(http_client, url, payload, headers, config, sleep_fn)
    finally:
        if owns_client:
            http_client.close()


def _post_with_retry(
    http_client: httpx.Client,
    url: str,
    payload: dict,
    headers: dict[str, str],
    config: OpenRouterSttConfig,
    sleep_fn: Callable[[float], None],
) -> SttResult:
    """지수 백오프로 재시도하며 STT 요청을 보낸다."""
    last_error: Exception | None = None
    for attempt in range(config.max_retries):
        try:
            resp = http_client.post(url, json=payload, headers=headers)
        except httpx.TransportError as exc:
            last_error = exc
            logger.warning("OpenRouter STT 전송 오류(시도 %d/%d): %s", attempt + 1, config.max_retries, exc)
        else:
            if resp.status_code == 200:
                return _parse_response(resp)
            if resp.status_code in _RETRYABLE_STATUS:
                last_error = TranscriptionError(f"OpenRouter STT 일시 오류 {resp.status_code}: {resp.text}")
                logger.warning(
                    "OpenRouter STT 재시도 가능 오류(시도 %d/%d): %s",
                    attempt + 1,
                    config.max_retries,
                    last_error,
                )
            else:
                raise TranscriptionError(f"OpenRouter STT 실패 {resp.status_code}: {resp.text}")
        if attempt < config.max_retries - 1:
            sleep_fn(config.backoff_base**attempt)

    raise TranscriptionError(f"OpenRouter STT 재시도를 모두 소진했습니다. 마지막 오류: {last_error}") from last_error


def _parse_response(resp: httpx.Response) -> SttResult:
    """200 응답 body 를 :class:`SttResult` 로 변환한다."""
    data = resp.json()
    text = data.get("text")
    if not text or not text.strip():
        raise TranscriptionError(f"OpenRouter STT 응답에 text 가 없습니다: {data}")
    duration = float(data.get("usage", {}).get("seconds", 0.0))
    return SttResult(text=text.strip(), duration_sec=duration)
