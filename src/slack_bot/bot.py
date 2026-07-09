"""Slack Socket Mode 봇: DM 오디오 업로드 → 전사+요약 처리 → 결과 파일 업로드.

이벤트 핸들러는 무거운 작업(다운로드/전사/요약)을 절대 동기로 처리하지 않는다 — Slack
Events API 는 3초 내 처리를 기대하며, 넘기면 이벤트가 재전송돼 같은 파일이 중복 처리될
수 있다. 따라서 핸들러는 ack 메시지만 즉시 보내고 실제 작업은 스레드풀로 넘긴다.

처리 대상 오디오 확장자(``config.slack.allowed_extensions``)가 첨부된 메시지만 반응한다
— ``bot_id`` 유무는 보지 않는다. 맥 Automation(Shortcuts)이 Voice Memo 를 봇 토큰으로
직접 업로드하는 경우 그 메시지의 업로더가 봇 자신으로 찍히는데, 이것도 처리 대상이다.
무한루프(봇이 올린 파일에 봇이 다시 반응) 위험은 이 확장자 필터만으로 이미 막힌다 —
봇이 결과로 올리는 파일은 항상 ``.md`` 라 ``allowed_extensions`` 에 없다. Slack 재전송으로
인한 중복 처리는 ``event_ts`` 기준 최근 이벤트 캐시로 막는다.
"""

from __future__ import annotations

import logging
import os
import sys
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler
from slack_sdk import WebClient

from src.exceptions import PipelineError, SlackBotError
from src.slack_bot.config import SlackBotConfig, load_slack_bot_config
from src.slack_bot.processor import SlackAudioFile, process_meeting_audio

logger = logging.getLogger(__name__)

# 최근 처리한 event_ts 를 이 개수만큼 기억해 Slack 재전송으로 인한 중복 처리를 막는다.
# 개인용 봇 기준으로 충분히 큰 값이면 되고, 무한정 쌓이지 않도록 상한을 둔다.
MAX_SEEN_EVENTS = 500
MAX_WORKERS = 2


def build_app(config: SlackBotConfig) -> App:
    """설정을 반영한 Slack Bolt ``App`` 을 만든다(이벤트 리스너 등록까지 포함)."""
    app = App(token=config.slack_bot_token)
    executor = ThreadPoolExecutor(max_workers=MAX_WORKERS)
    seen_events: OrderedDict[str, None] = OrderedDict()

    @app.event("message")
    def handle_message(event: dict[str, Any], say: Any, client: WebClient) -> None:
        if event.get("channel_type") != "im":
            return  # DM 만 처리 대상(채널 업로드는 스코프 밖)

        audio_file = _extract_audio_file(event, config)
        if audio_file is None:
            return  # 오디오 첨부가 없으면(봇 자신의 ack/실패 안내 메시지 포함) 무시

        event_id = event.get("event_ts") or event.get("client_msg_id")
        if event_id and _already_seen(seen_events, event_id):
            logger.info("중복 이벤트 무시: %s", event_id)
            return

        logger.info("오디오 파일 수신: %s", audio_file.name)
        say(text=config.slack.ack_message, thread_ts=event.get("ts"))
        executor.submit(_process_and_reply, event, audio_file, config, client)

    return app


def _already_seen(seen: OrderedDict[str, None], event_id: str) -> bool:
    """``event_id`` 를 이미 봤으면 ``True``. 처음이면 기록하고 ``False``.

    오래된 항목은 ``MAX_SEEN_EVENTS`` 를 넘으면 가장 오래된 것부터 밀어낸다(메모리 누수 방지).
    """
    if event_id in seen:
        return True
    seen[event_id] = None
    if len(seen) > MAX_SEEN_EVENTS:
        seen.popitem(last=False)
    return False


def _extract_audio_file(event: dict[str, Any], config: SlackBotConfig) -> SlackAudioFile | None:
    """이벤트 첨부파일 중 처리 대상 오디오를 하나 뽑는다(없거나 확장자 미지원이면 ``None``)."""
    for file_info in event.get("files") or []:
        name = file_info.get("name", "")
        ext = Path(name).suffix.lower()
        if ext in config.slack.allowed_extensions:
            return SlackAudioFile(url_private=file_info["url_private"], name=name, extension=ext)
    return None


def _process_and_reply(
    event: dict[str, Any],
    audio_file: SlackAudioFile,
    config: SlackBotConfig,
    client: WebClient,
) -> None:
    """백그라운드 스레드에서 처리하고 결과를 DM 스레드에 파일로 올린다.

    워커 스레드에서 발생한 예외는 스레드 밖으로 전파되지 않으므로(ThreadPoolExecutor.submit
    은 조용히 삼킴), 여기서 반드시 잡아 Slack 메시지로 실패를 알린다 — 그러지 않으면
    사용자는 봇이 응답 없이 멈춘 것처럼 보게 된다.
    """
    channel = event["channel"]
    thread_ts = event.get("ts")
    try:
        output_path = process_meeting_audio(audio_file, config=config)
    except (PipelineError, SlackBotError) as exc:
        logger.error("처리 실패: %s (%s)", audio_file.name, exc)
        client.chat_postMessage(channel=channel, thread_ts=thread_ts, text=f"❌ 처리 실패: {exc}")
        return
    except Exception:  # noqa: BLE001 - 워커 스레드가 예외로 죽어 조용히 무응답되는 것을 막는다
        logger.exception("예상치 못한 오류로 처리 실패: %s", audio_file.name)
        client.chat_postMessage(
            channel=channel,
            thread_ts=thread_ts,
            text="❌ 예상치 못한 오류로 처리에 실패했습니다(로그 확인 필요).",
        )
        return

    client.files_upload_v2(
        channel=channel,
        thread_ts=thread_ts,
        file=str(output_path),
        filename=output_path.name,
        title=output_path.stem,
        initial_comment="✅ 회의 요약이 완료됐습니다.",
    )


def _install_socket_error_watchdog(client: Any, config: SlackBotConfig) -> None:
    """웹소켓 에러가 짧은 시간에 몰리면 프로세스를 종료해 launchd 가 재시작하게 한다.

    맥이 절전(디스플레이 꺼짐)에서 깨어나면 Socket Mode 웹소켓이 좀비 상태가 되어, 재연결
    직후 즉시 ``BrokenPipeError`` 가 나는 무한루프에 빠질 수 있다(2026-07-09 실제 발생,
    프로세스는 살아있어 launchd ``KeepAlive`` 가 개입하지 않고 몇 시간이고 방치됨). 임계값을
    넘으면 ``os._exit`` 로 즉시 종료해 launchd 가 새 프로세스로 재시작하게 한다.
    """
    window_sec = config.socket_mode.error_storm_window_sec
    threshold = config.socket_mode.error_storm_threshold
    failure_times: list[float] = []

    def on_error(_exc: Exception) -> None:
        now = time.monotonic()
        failure_times.append(now)
        while failure_times and now - failure_times[0] > window_sec:
            failure_times.pop(0)
        if len(failure_times) >= threshold:
            logger.error(
                "웹소켓 재연결 실패가 %.0f초 내 %d회 발생 — 프로세스를 재시작합니다(launchd KeepAlive).",
                window_sec,
                len(failure_times),
            )
            os._exit(1)  # ponytail: 정상 종료(스레드/소켓 정리) 대신 즉시 종료 — 재시작은 launchd 에 맡긴다

    client.on_error_listeners.append(on_error)


def run() -> None:
    """설정을 로드하고 Socket Mode 로 봇을 시작한다(블로킹, ``SIGINT`` 까지 실행)."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stdout,  # ponytail: basicConfig 기본 핸들러는 stderr라 launchd의 out/err 로그 분리가 깨짐
    )
    config = load_slack_bot_config()
    app = build_app(config)
    handler = SocketModeHandler(app, config.slack_app_token)
    _install_socket_error_watchdog(handler.client, config)
    logger.info("Slack 봇 시작 (Socket Mode)")
    handler.start()
