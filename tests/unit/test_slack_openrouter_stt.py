"""OpenRouter STT 클라이언트 단위 테스트: 요청 포맷, 재시도, 에러 매핑.

실제 네트워크를 타지 않도록 ``httpx.MockTransport`` 로 응답을 흉내낸다.
"""

from __future__ import annotations

import base64
import json

import httpx
import pytest

from src.exceptions import TranscriptionError
from src.slack_bot.config import OpenRouterSttConfig
from src.slack_bot.openrouter_stt import transcribe_audio

NO_SLEEP = lambda _: None  # noqa: E731 - 테스트용 no-op 백오프


def _config(**overrides) -> OpenRouterSttConfig:
    base = {
        "model": "microsoft/mai-transcribe-1.5",
        "base_url": "https://openrouter.ai/api/v1",
        "language": "ko",
        "request_timeout_sec": 10,
        "max_retries": 3,
        "backoff_base": 2.0,
        "segment_minutes": 15,
        "segment_overlap_sec": 30,
    }
    base.update(overrides)
    return OpenRouterSttConfig(**base)


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_transcribe_audio_sends_base64_json_payload():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        captured["auth"] = request.headers.get("authorization")
        captured["url"] = str(request.url)
        return httpx.Response(200, json={"text": "안녕하세요", "usage": {"seconds": 12.5}})

    result = transcribe_audio(
        b"raw-audio-bytes",
        audio_format="m4a",
        config=_config(),
        api_key="test-key",
        client=_client(handler),
        sleep_fn=NO_SLEEP,
    )

    assert result.text == "안녕하세요"
    assert result.duration_sec == 12.5
    assert captured["auth"] == "Bearer test-key"
    assert captured["url"] == "https://openrouter.ai/api/v1/audio/transcriptions"
    assert captured["body"]["model"] == "microsoft/mai-transcribe-1.5"
    assert captured["body"]["language"] == "ko"
    assert captured["body"]["input_audio"]["format"] == "m4a"
    # base64 페이로드가 원본 바이트를 그대로 복원 가능해야 한다(raw bytes, data URI 아님).
    decoded = base64.b64decode(captured["body"]["input_audio"]["data"])
    assert decoded == b"raw-audio-bytes"


def test_transcribe_audio_retries_on_429_then_succeeds():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(429, text="rate limited")
        return httpx.Response(200, json={"text": "결과", "usage": {"seconds": 1.0}})

    result = transcribe_audio(
        b"x",
        audio_format="wav",
        config=_config(max_retries=3),
        api_key="k",
        client=_client(handler),
        sleep_fn=NO_SLEEP,
    )

    assert result.text == "결과"
    assert calls["n"] == 3


def test_transcribe_audio_exhausts_retries_raises_transcription_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="unavailable")

    with pytest.raises(TranscriptionError):
        transcribe_audio(
            b"x",
            audio_format="wav",
            config=_config(max_retries=2),
            api_key="k",
            client=_client(handler),
            sleep_fn=NO_SLEEP,
        )


def test_transcribe_audio_non_retryable_error_fails_immediately():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(400, text="bad request")

    with pytest.raises(TranscriptionError):
        transcribe_audio(
            b"x",
            audio_format="wav",
            config=_config(max_retries=3),
            api_key="k",
            client=_client(handler),
            sleep_fn=NO_SLEEP,
        )

    assert calls["n"] == 1  # 재시도 없이 즉시 실패


def test_transcribe_audio_empty_text_raises_transcription_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"text": "  "})

    with pytest.raises(TranscriptionError):
        transcribe_audio(
            b"x",
            audio_format="wav",
            config=_config(max_retries=1),
            api_key="k",
            client=_client(handler),
            sleep_fn=NO_SLEEP,
        )


def test_transcribe_audio_missing_usage_defaults_duration_zero():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"text": "결과"})

    result = transcribe_audio(
        b"x",
        audio_format="wav",
        config=_config(),
        api_key="k",
        client=_client(handler),
        sleep_fn=NO_SLEEP,
    )

    assert result.duration_sec == 0.0
