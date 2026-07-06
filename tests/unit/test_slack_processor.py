"""Slack 오디오 처리 오케스트레이션(``process_meeting_audio``) 단위 테스트.

다운로드(httpx)/전사(OpenRouter STT)/요약(summarize_meeting)/설정 로딩을 모두 테스트
더블로 대체해, 순수 오케스트레이션 로직(청크 조립 → 리포트 저장)만 검증한다.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from src.exceptions import SlackBotError, TranscriptionError
from src.slack_bot.audio_split import AudioSegment
from src.slack_bot.config import OpenRouterSttConfig, SlackBotConfig, SlackConfig
from src.slack_bot.openrouter_stt import SttResult
from src.slack_bot.processor import SlackAudioFile, process_meeting_audio
from src.summarize import SummaryResult


def _slack_bot_config(tmp_path: Path) -> SlackBotConfig:
    return SlackBotConfig(
        slack=SlackConfig(
            allowed_extensions=(".m4a",),
            output_dir=tmp_path / "slack_output",
            ack_message="접수",
        ),
        stt=OpenRouterSttConfig(
            model="microsoft/mai-transcribe-1.5",
            base_url="https://openrouter.ai/api/v1",
            language="ko",
            request_timeout_sec=10,
            max_retries=1,
            backoff_base=2.0,
            segment_minutes=15,
            segment_overlap_sec=30,
        ),
        slack_bot_token="xoxb-test",
        slack_app_token="xapp-test",
        openrouter_api_key="or-test",
    )


def _patch_single_segment(monkeypatch, *, duration_sec: float = 125.0) -> None:
    """오디오 분할을 건너뛰게 만든다(짧은 오디오 취급) — ffmpeg/ffprobe 를 실제로 부르지 않는다."""
    monkeypatch.setattr("src.slack_bot.processor.probe_duration_sec", lambda audio_bytes, ext: duration_sec)
    monkeypatch.setattr(
        "src.slack_bot.processor.split_audio",
        lambda audio_bytes, **kw: [AudioSegment(data=audio_bytes, start_sec=0.0)],
    )


def _fake_pipeline_config():
    return SimpleNamespace(
        summarize=object(),
        api_key="or-test",
        chunking=SimpleNamespace(single_shot_max_chars=40000),
    )


def _audio_file() -> SlackAudioFile:
    return SlackAudioFile(url_private="https://files.slack.com/private/audio.m4a", name="회의.m4a", extension=".m4a")


def test_process_meeting_audio_writes_report_and_returns_path(tmp_path, monkeypatch):
    _patch_single_segment(monkeypatch)
    monkeypatch.setattr("src.slack_bot.processor.load_config", lambda path: _fake_pipeline_config())
    monkeypatch.setattr(
        "src.slack_bot.processor.transcribe_audio",
        lambda audio_bytes, **kw: SttResult(text="회의 내용 전사본", duration_sec=125.0),
    )
    monkeypatch.setattr(
        "src.slack_bot.processor.summarize_meeting",
        lambda chunks, **kw: SummaryResult(body="## 핵심 요약\n- 내용", model="test-model"),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("authorization") == "Bearer xoxb-test"
        return httpx.Response(200, content=b"binary-audio-bytes")

    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    config = _slack_bot_config(tmp_path)

    output_path = process_meeting_audio(_audio_file(), config=config, http_client=http_client)

    assert output_path == config.slack.output_dir / "회의.md"
    content = output_path.read_text(encoding="utf-8")
    assert "핵심 요약" in content
    assert "test-model" in content
    assert "회의.m4a" in content


def test_process_meeting_audio_maps_qta_extension_to_m4a_format(tmp_path, monkeypatch):
    _patch_single_segment(monkeypatch)
    monkeypatch.setattr("src.slack_bot.processor.load_config", lambda path: _fake_pipeline_config())
    captured_format = {}

    def fake_transcribe(audio_bytes, **kw):
        captured_format["value"] = kw["audio_format"]
        return SttResult(text="회의 내용 전사본", duration_sec=125.0)

    monkeypatch.setattr("src.slack_bot.processor.transcribe_audio", fake_transcribe)
    monkeypatch.setattr(
        "src.slack_bot.processor.summarize_meeting",
        lambda chunks, **kw: SummaryResult(body="## 핵심 요약\n- 내용", model="test-model"),
    )

    http_client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=b"bytes")))
    config = _slack_bot_config(tmp_path)
    audio_file = SlackAudioFile(
        url_private="https://files.slack.com/private/audio.qta", name="회의.qta", extension=".qta"
    )

    process_meeting_audio(audio_file, config=config, http_client=http_client)

    assert captured_format["value"] == "m4a"


def test_process_meeting_audio_transcribes_each_split_segment_and_builds_multi_chunk_summary(tmp_path, monkeypatch):
    monkeypatch.setattr("src.slack_bot.processor.load_config", lambda path: _fake_pipeline_config())
    monkeypatch.setattr("src.slack_bot.processor.probe_duration_sec", lambda audio_bytes, ext: 2700.0)
    monkeypatch.setattr(
        "src.slack_bot.processor.split_audio",
        lambda audio_bytes, **kw: [
            AudioSegment(data=b"segment-0", start_sec=0.0),
            AudioSegment(data=b"segment-1", start_sec=900.0),
            AudioSegment(data=b"segment-2", start_sec=1800.0),
        ],
    )

    seen_segment_bytes = []

    def fake_transcribe(audio_bytes, **kw):
        seen_segment_bytes.append(audio_bytes)
        return SttResult(text=f"전사-{audio_bytes.decode()}", duration_sec=900.0)

    monkeypatch.setattr("src.slack_bot.processor.transcribe_audio", fake_transcribe)

    captured_chunks = {}

    def fake_summarize(chunks, **kw):
        captured_chunks["value"] = chunks
        return SummaryResult(body="## 핵심 요약\n- 내용", model="test-model")

    monkeypatch.setattr("src.slack_bot.processor.summarize_meeting", fake_summarize)

    http_client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=b"bytes")))
    config = _slack_bot_config(tmp_path)

    process_meeting_audio(_audio_file(), config=config, http_client=http_client)

    assert seen_segment_bytes == [b"segment-0", b"segment-1", b"segment-2"]
    chunks = captured_chunks["value"]
    assert [c.index for c in chunks] == [0, 1, 2]
    assert [c.start for c in chunks] == [0.0, 900.0, 1800.0]
    assert chunks[0].text == "전사-segment-0"


def test_process_meeting_audio_raises_slack_bot_error_on_download_failure(tmp_path, monkeypatch):
    monkeypatch.setattr("src.slack_bot.processor.load_config", lambda path: _fake_pipeline_config())

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="forbidden")

    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    config = _slack_bot_config(tmp_path)

    with pytest.raises(SlackBotError):
        process_meeting_audio(_audio_file(), config=config, http_client=http_client)


def test_process_meeting_audio_propagates_transcription_error(tmp_path, monkeypatch):
    _patch_single_segment(monkeypatch)
    monkeypatch.setattr("src.slack_bot.processor.load_config", lambda path: _fake_pipeline_config())

    def _raise_transcription_error(audio_bytes, **kw):
        raise TranscriptionError("STT 실패")

    monkeypatch.setattr("src.slack_bot.processor.transcribe_audio", _raise_transcription_error)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"bytes")

    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    config = _slack_bot_config(tmp_path)

    with pytest.raises(TranscriptionError):
        process_meeting_audio(_audio_file(), config=config, http_client=http_client)
