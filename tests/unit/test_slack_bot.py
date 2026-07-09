"""Slack 봇 이벤트 처리 로직 단위 테스트: 중복 이벤트 가드, 오디오 파일 추출,
백그라운드 처리 결과 전달.

``App``/``SocketModeHandler`` 는 실제 Slack 인증(auth.test) 을 유발하므로 인스턴스화하지
않는다 — 순수 헬퍼 함수와 ``_process_and_reply`` 오케스트레이션만 테스트한다.
"""

from __future__ import annotations

from collections import OrderedDict
from pathlib import Path

from src.exceptions import PipelineError, SlackBotError
from src.slack_bot.bot import (
    MAX_SEEN_EVENTS,
    _already_seen,
    _extract_audio_file,
    _install_socket_error_watchdog,
    _process_and_reply,
)
from src.slack_bot.config import (
    OpenRouterSttConfig,
    SlackBotConfig,
    SlackConfig,
    SocketModeConfig,
)
from src.slack_bot.processor import SlackAudioFile

# --- _already_seen --------------------------------------------------------------


def test_already_seen_false_on_first_sighting_true_on_repeat():
    seen: OrderedDict[str, None] = OrderedDict()
    assert _already_seen(seen, "evt-1") is False
    assert _already_seen(seen, "evt-1") is True


def test_already_seen_evicts_oldest_beyond_capacity():
    seen: OrderedDict[str, None] = OrderedDict()
    for i in range(MAX_SEEN_EVENTS + 10):
        _already_seen(seen, f"evt-{i}")
    assert len(seen) <= MAX_SEEN_EVENTS
    assert "evt-0" not in seen  # 가장 오래된 항목은 밀려남


# --- _extract_audio_file ---------------------------------------------------------


def _config(allowed=(".m4a", ".wav")) -> SlackBotConfig:
    return SlackBotConfig(
        slack=SlackConfig(allowed_extensions=allowed, output_dir=Path("/tmp/out"), ack_message="접수"),
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
        socket_mode=SocketModeConfig(error_storm_window_sec=60.0, error_storm_threshold=5),
        slack_bot_token="xoxb-t",
        slack_app_token="xapp-t",
        openrouter_api_key="k",
    )


def test_extract_audio_file_returns_none_when_no_files():
    assert _extract_audio_file({"files": []}, _config()) is None
    assert _extract_audio_file({}, _config()) is None


def test_extract_audio_file_ignores_unsupported_extension():
    event = {"files": [{"name": "notes.txt", "url_private": "https://x/notes.txt"}]}
    assert _extract_audio_file(event, _config()) is None


def test_extract_audio_file_finds_supported_audio():
    event = {
        "files": [
            {"name": "notes.txt", "url_private": "https://x/notes.txt"},
            {"name": "회의.m4a", "url_private": "https://x/회의.m4a"},
        ]
    }
    result = _extract_audio_file(event, _config())
    assert result is not None
    assert result.name == "회의.m4a"
    assert result.extension == ".m4a"
    assert result.url_private == "https://x/회의.m4a"


def test_extract_audio_file_ignores_bot_id_and_subtype():
    """맥 Automation 이 봇 토큰으로 직접 올린 파일(bot_id 있음)도 처리 대상이어야 한다 —
    확장자 필터만으로 무한루프를 막으므로 bot_id/subtype 은 판단에 관여하지 않는다."""
    event = {
        "bot_id": "B0BFDJ61E4U",
        "subtype": "bot_message",
        "files": [{"name": "회의.m4a", "url_private": "https://x/회의.m4a"}],
    }
    result = _extract_audio_file(event, _config())
    assert result is not None
    assert result.name == "회의.m4a"


# --- _process_and_reply -----------------------------------------------------------


class FakeSlackClient:
    """``chat_postMessage``/``files_upload_v2`` 호출을 기록하는 테스트 더블."""

    def __init__(self) -> None:
        self.posted: list[dict] = []
        self.uploaded: list[dict] = []

    def chat_postMessage(self, **kwargs):  # noqa: N802 - slack_sdk 메서드명 규칙 그대로 따름
        self.posted.append(kwargs)

    def files_upload_v2(self, **kwargs):  # noqa: N802
        self.uploaded.append(kwargs)


def _audio_file() -> SlackAudioFile:
    return SlackAudioFile(url_private="https://x/audio.m4a", name="audio.m4a", extension=".m4a")


def test_process_and_reply_uploads_file_on_success(monkeypatch, tmp_path):
    report_path = tmp_path / "audio.md"
    report_path.write_text("# 회의 요약\n", encoding="utf-8")
    monkeypatch.setattr("src.slack_bot.bot.process_meeting_audio", lambda audio_file, config: report_path)

    client = FakeSlackClient()
    event = {"channel": "D123", "ts": "111.222"}
    _process_and_reply(event, _audio_file(), _config(), client)

    assert len(client.uploaded) == 1
    assert client.uploaded[0]["channel"] == "D123"
    assert client.uploaded[0]["file"] == str(report_path)
    assert not client.posted  # 성공 시 실패 메시지는 보내지 않는다


def test_process_and_reply_posts_failure_message_on_pipeline_error(monkeypatch):
    def _raise(audio_file, config):
        raise PipelineError("전사 실패")

    monkeypatch.setattr("src.slack_bot.bot.process_meeting_audio", _raise)

    client = FakeSlackClient()
    event = {"channel": "D123", "ts": "111.222"}
    _process_and_reply(event, _audio_file(), _config(), client)

    assert not client.uploaded
    assert len(client.posted) == 1
    assert "전사 실패" in client.posted[0]["text"]


def test_process_and_reply_handles_slack_bot_error(monkeypatch):
    def _raise(audio_file, config):
        raise SlackBotError("다운로드 실패")

    monkeypatch.setattr("src.slack_bot.bot.process_meeting_audio", _raise)

    client = FakeSlackClient()
    _process_and_reply({"channel": "D1", "ts": "1.1"}, _audio_file(), _config(), client)

    assert "다운로드 실패" in client.posted[0]["text"]


def test_process_and_reply_does_not_crash_worker_thread_on_unexpected_exception(monkeypatch):
    def _raise(audio_file, config):
        raise RuntimeError("bug")

    monkeypatch.setattr("src.slack_bot.bot.process_meeting_audio", _raise)

    client = FakeSlackClient()
    # 예외가 여기서 전파되면 테스트가 실패한다 — 워커 스레드 안에서 삼켜져 Slack 메시지로
    # 변환돼야 한다는 것이 이 테스트의 핵심 단언이다.
    _process_and_reply({"channel": "D1", "ts": "1.1"}, _audio_file(), _config(), client)

    assert len(client.posted) == 1
    assert "예상치 못한 오류" in client.posted[0]["text"]


# --- _install_socket_error_watchdog ------------------------------------------


class _FakeSocketClient:
    def __init__(self):
        self.on_error_listeners = []


def test_socket_error_watchdog_exits_when_errors_exceed_threshold_in_window(monkeypatch):
    exit_codes = []
    monkeypatch.setattr("src.slack_bot.bot.os._exit", exit_codes.append)

    client = _FakeSocketClient()
    config = _config()  # error_storm_window_sec=60, error_storm_threshold=5
    _install_socket_error_watchdog(client, config)
    on_error = client.on_error_listeners[0]

    for _ in range(4):
        on_error(Exception("boom"))
    assert exit_codes == []  # 임계값 미달이면 종료하지 않는다

    on_error(Exception("boom"))
    assert exit_codes == [1]  # 5번째(윈도우 내 임계값 도달)에 종료


def test_socket_error_watchdog_does_not_exit_when_errors_are_spread_outside_window(monkeypatch):
    exit_codes = []
    monkeypatch.setattr("src.slack_bot.bot.os._exit", exit_codes.append)

    fake_now = [0.0]
    monkeypatch.setattr("src.slack_bot.bot.time.monotonic", lambda: fake_now[0])

    client = _FakeSocketClient()
    config = _config()  # error_storm_window_sec=60, error_storm_threshold=5
    _install_socket_error_watchdog(client, config)
    on_error = client.on_error_listeners[0]

    for _ in range(4):
        on_error(Exception("boom"))
        fake_now[0] += 61  # 매번 윈도우 밖으로 밀려나 실패가 누적되지 않는다

    assert exit_codes == []
