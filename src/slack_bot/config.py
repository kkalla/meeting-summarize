"""Slack 봇 설정 로딩: ``configs/slack_bot.yaml`` 파싱 + ``.env`` 시크릿.

``src.config`` 의 ``PipelineConfig`` 와는 별개 트리다(스코프 분리). 다만 요약 단계는
``configs/pipeline.yaml`` 의 ``summarize`` 섹션을 그대로 재사용하므로, API 키
(``OPENROUTER_API_KEY``)는 두 설정이 같은 값을 공유한다.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml
from dotenv import load_dotenv

from src.config import PROJECT_ROOT
from src.exceptions import DependencyError

ENV_SLACK_BOT_TOKEN = "SLACK_BOT_TOKEN"
ENV_SLACK_APP_TOKEN = "SLACK_APP_TOKEN"
ENV_OPENROUTER_API_KEY = "OPENROUTER_API_KEY"
DEFAULT_CONFIG_PATH = "configs/slack_bot.yaml"


def _resolve_dir(value: str) -> Path:
    """상대경로를 프로젝트 루트 기준으로 절대화한다(``src.config._resolve_dir`` 와 동일 규칙).

    별도 모듈에서 같은 규칙을 재구현하는 이유는 ``src.config`` 의 private 헬퍼에 의존하지
    않고 Slack 봇 설정 트리를 독립적으로 유지하기 위함이다.
    """
    path = Path(value)
    if path.is_absolute():
        return path
    return PROJECT_ROOT / path


@dataclass(frozen=True)
class SlackConfig:
    """Slack 이벤트 처리/응답 관련 설정."""

    allowed_extensions: tuple[str, ...]
    output_dir: Path
    ack_message: str

    def __post_init__(self) -> None:
        if not self.allowed_extensions:
            raise DependencyError("slack.allowed_extensions 가 비어 있습니다 — 처리할 확장자를 하나 이상 지정하세요.")
        if not self.ack_message.strip():
            raise DependencyError("slack.ack_message 가 비어 있습니다.")


@dataclass(frozen=True)
class OpenRouterSttConfig:
    """OpenRouter 오디오 전사(STT) 설정.

    ``src.config.SttConfig``(로컬 whisper.cpp)와 이름이 겹치지 않도록 접두어를 붙였다.
    """

    model: str
    base_url: str
    language: str
    request_timeout_sec: int
    max_retries: int
    backoff_base: float
    segment_minutes: int
    segment_overlap_sec: int

    def __post_init__(self) -> None:
        if not self.model.strip():
            raise DependencyError("stt.model 이 비어 있습니다.")
        if self.max_retries < 1:
            raise DependencyError(f"stt.max_retries 는 1 이상이어야 합니다: {self.max_retries}")
        if self.request_timeout_sec < 1:
            raise DependencyError(f"stt.request_timeout_sec 는 1 이상이어야 합니다: {self.request_timeout_sec}")
        if self.segment_minutes < 1:
            raise DependencyError(f"stt.segment_minutes 는 1 이상이어야 합니다: {self.segment_minutes}")
        if self.segment_overlap_sec < 0:
            raise DependencyError(f"stt.segment_overlap_sec 는 0 이상이어야 합니다: {self.segment_overlap_sec}")


@dataclass(frozen=True)
class SocketModeConfig:
    """Socket Mode 웹소켓 재연결 폭주 감지(자가 종료 → launchd 재시작) 설정."""

    error_storm_window_sec: float
    error_storm_threshold: int

    def __post_init__(self) -> None:
        if self.error_storm_window_sec <= 0:
            raise DependencyError(
                f"socket_mode.error_storm_window_sec 는 0보다 커야 합니다: {self.error_storm_window_sec}"
            )
        if self.error_storm_threshold < 1:
            raise DependencyError(
                f"socket_mode.error_storm_threshold 는 1 이상이어야 합니다: {self.error_storm_threshold}"
            )


@dataclass(frozen=True)
class SlackBotConfig:
    """Slack 봇 전체 설정 + 런타임 시크릿(토큰/API 키)."""

    slack: SlackConfig
    stt: OpenRouterSttConfig
    socket_mode: SocketModeConfig
    slack_bot_token: str
    slack_app_token: str
    openrouter_api_key: str


def load_slack_bot_config(
    config_path: str | os.PathLike[str] = DEFAULT_CONFIG_PATH,
) -> SlackBotConfig:
    """YAML 설정과 ``.env`` 를 읽어 :class:`SlackBotConfig` 를 만든다.

    Args:
        config_path: slack_bot.yaml 경로.

    Returns:
        검증된 설정 객체.

    Raises:
        DependencyError: 설정 파일이 없거나 필수 환경변수가 비어 있을 때.
    """
    path = Path(config_path)
    if not path.is_file() and not path.is_absolute():
        path = PROJECT_ROOT / config_path
    if not path.is_file():
        raise DependencyError(f"설정 파일을 찾을 수 없습니다: {path}. configs/slack_bot.yaml 을 확인하세요.")

    with path.open("r", encoding="utf-8") as fp:
        raw = yaml.safe_load(fp) or {}

    load_dotenv()
    bot_token = os.environ.get(ENV_SLACK_BOT_TOKEN, "").strip()
    app_token = os.environ.get(ENV_SLACK_APP_TOKEN, "").strip()
    api_key = os.environ.get(ENV_OPENROUTER_API_KEY, "").strip()
    missing = [
        name
        for name, value in (
            (ENV_SLACK_BOT_TOKEN, bot_token),
            (ENV_SLACK_APP_TOKEN, app_token),
            (ENV_OPENROUTER_API_KEY, api_key),
        )
        if not value
    ]
    if missing:
        raise DependencyError(
            f"다음 환경변수가 설정되지 않았습니다: {', '.join(missing)}. "
            ".env 에 SLACK_BOT_TOKEN(xoxb-...), SLACK_APP_TOKEN(xapp-...), "
            "OPENROUTER_API_KEY 를 채워넣으세요."
        )

    return _build_config(raw, bot_token=bot_token, app_token=app_token, api_key=api_key)


def _build_config(raw: dict, *, bot_token: str, app_token: str, api_key: str) -> SlackBotConfig:
    """파싱된 dict 를 dataclass 트리로 변환한다.

    Raises:
        DependencyError: YAML 에 필수 섹션/키가 없거나 값 타입이 잘못됐을 때.
    """
    try:
        slack_raw = raw["slack"]
        stt_raw = raw["stt"]
        socket_mode_raw = raw["socket_mode"]
        return SlackBotConfig(
            slack=SlackConfig(
                allowed_extensions=tuple(str(ext).lower() for ext in slack_raw["allowed_extensions"]),
                output_dir=_resolve_dir(str(slack_raw["output_dir"])),
                ack_message=str(slack_raw["ack_message"]),
            ),
            stt=OpenRouterSttConfig(
                model=str(stt_raw["model"]),
                base_url=str(stt_raw["base_url"]),
                language=str(stt_raw["language"]),
                request_timeout_sec=int(stt_raw["request_timeout_sec"]),
                max_retries=int(stt_raw["max_retries"]),
                backoff_base=float(stt_raw["backoff_base"]),
                segment_minutes=int(stt_raw["segment_minutes"]),
                segment_overlap_sec=int(stt_raw["segment_overlap_sec"]),
            ),
            socket_mode=SocketModeConfig(
                error_storm_window_sec=float(socket_mode_raw["error_storm_window_sec"]),
                error_storm_threshold=int(socket_mode_raw["error_storm_threshold"]),
            ),
            slack_bot_token=bot_token,
            slack_app_token=app_token,
            openrouter_api_key=api_key,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise DependencyError(
            f"slack_bot.yaml 설정이 올바르지 않습니다({type(exc).__name__}: {exc}). "
            "configs/slack_bot.yaml 의 필수 키와 값 타입을 확인하세요."
        ) from exc
